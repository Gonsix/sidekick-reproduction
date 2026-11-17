// HTTP/3 POST over a real quiche TLS/QUIC connection.
mod sidekick_support;
use quiche::h3::NameValue;
use sidekick_support::*;
use std::net::UdpSocket;
use std::time::{Duration, Instant};

fn run() -> Result<()> {
    let a = Args::new();
    let server = a.get("role", "sender") == "receiver";
    let bind = address(
        &a,
        "bind",
        if server {
            "0.0.0.0:4433"
        } else {
            "0.0.0.0:44330"
        },
    );
    let peer = address(&a, "peer", "127.0.0.1:4433");
    let amount = a.num("bytes", 10_000_000);
    let timeout = Duration::from_secs(a.num("timeout", 120));
    let socket = UdpSocket::bind(bind)?;
    socket.set_nonblocking(true)?;
    let local = socket.local_addr()?;
    let mut config = quiche::Config::new(quiche::PROTOCOL_VERSION)?;
    config.set_application_protos(quiche::h3::APPLICATION_PROTOCOL)?;
    config.set_cc_algorithm(quiche::CongestionControlAlgorithm::CUBIC);
    config.enable_hystart(false);
    config.set_max_idle_timeout(timeout.as_millis() as u64);
    config.set_max_recv_udp_payload_size(1350);
    config.set_max_send_udp_payload_size(1350);
    // Identical generous flow-control limits in every ablation; buffers remain
    // bounded at endpoints. This experiment measures congestion/ACK behavior.
    config.set_initial_max_data(64_000_000);
    config.set_initial_max_stream_data_bidi_local(64_000_000);
    config.set_initial_max_stream_data_bidi_remote(64_000_000);
    config.set_initial_max_stream_data_uni(1_000_000);
    config.set_initial_max_streams_bidi(16);
    config.set_initial_max_streams_uni(16);
    config.set_max_ack_delay(a.num("ack-delay-ms", 0).max(25));
    config.verify_peer(false); // Controlled lab self-signed certificate; TLS stays end-to-end.
    if server {
        config.load_cert_chain_from_pem_file(
            &a.get("cert", "reproduction/cert.pem"),
        )?;
        config
            .load_priv_key_from_pem_file(&a.get("key", "reproduction/key.pem"))?;
    }
    let mut feedback = if server { None } else { Feedback::new(&a)? };
    let start = Instant::now();
    let mut conn = if server {
        None
    } else {
        let id = random16();
        let mut c = quiche::connect(
            Some("localhost"),
            &quiche::ConnectionId::from_ref(&id),
            local,
            peer,
            &mut config,
        )?;
        c.configure_sidekick(
            a.options(false),
            a.values
                .get("token")
                .map(|_| a.token("token"))
                .unwrap_or([0; 16]),
        )
        .map_err(|e| format!("Sidekick configuration {:?}", e))?;
        Some(c)
    };
    let mut h3 = None;
    let mut request = None;
    let mut sent = 0u64;
    let mut received = 0u64;
    let mut digest = ring::digest::Context::new(&ring::digest::SHA256);
    let mut body = [7u8; 16_384];
    let mut output = [0u8; 1350];
    let mut input = [0u8; 65536];
    let mut verified = false;
    let mut response = false;
    let mut result_digest = String::new();
    let mut request_start = None;
    loop {
        if start.elapsed() > timeout {
            return Err(format!(
                "HTTP/3 timeout, sent={}, received={}",
                sent, received
            )
            .into());
        }
        loop {
            match socket.recv_from(&mut input) {
                Ok((n, from)) => {
                    if conn.is_none() {
                        let hdr = quiche::Header::from_slice(
                            &mut input[..n],
                            quiche::MAX_CONN_ID_LEN,
                        )?;
                        conn = Some(quiche::accept(
                            &hdr.dcid,
                            None,
                            local,
                            from,
                            &mut config,
                        )?);
                        if a.num("ack-delay-ms", 0) > 0 {
                            conn.as_mut().unwrap().set_experimental_ack_interval(
                                Some(Duration::from_millis(
                                    a.num("ack-delay-ms", 0),
                                )),
                            );
                        }
                    }
                    let c = conn.as_mut().unwrap();
                    match c.recv(
                        &mut input[..n],
                        quiche::RecvInfo { from, to: local },
                    ) {
                        Ok(_) | Err(quiche::Error::Done) => {},
                        Err(e) => {
                            return Err(format!("QUIC receive {:?}", e).into())
                        },
                    }
                },
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(e) => return Err(e.into()),
            }
        }
        if let Some(c) = conn.as_mut() {
            if let Some(f) = feedback.as_mut() {
                f.quic(c)?;
            }
            if c.timeout().map_or(false, |d| d.is_zero()) {
                c.on_timeout();
            }
            if c.is_established() && h3.is_none() {
                h3 = Some(quiche::h3::Connection::with_transport(
                    c,
                    &quiche::h3::Config::new()?,
                )?);
            }
            if let Some(h) = h3.as_mut() {
                if !server && request.is_none() {
                    let size = amount.to_string();
                    let headers = [
                        quiche::h3::Header::new(b":method", b"POST"),
                        quiche::h3::Header::new(b":scheme", b"https"),
                        quiche::h3::Header::new(b":authority", b"localhost"),
                        quiche::h3::Header::new(b":path", b"/upload"),
                        quiche::h3::Header::new(
                            b"content-length",
                            size.as_bytes(),
                        ),
                    ];
                    match h.send_request(c, &headers, amount == 0) {
                        Ok(id) => {
                            request = Some(id);
                            request_start = Some(Instant::now());
                        },
                        Err(quiche::h3::Error::StreamBlocked)
                        | Err(quiche::h3::Error::TransportError(
                            quiche::Error::Done,
                        )) => {},
                        Err(e) => {
                            return Err(format!("HTTP/3 request {:?}", e).into())
                        },
                    }
                }
                if !server {
                    if let Some(id) = request {
                        for _ in 0..128 {
                            if sent >= amount {
                                break;
                            }
                            let n =
                                (amount - sent).min(body.len() as u64) as usize;
                            match h.send_body(
                                c,
                                id,
                                &body[..n],
                                sent + n as u64 == amount,
                            ) {
                                Ok(w) => {
                                    sent += w as u64;
                                },
                                Err(quiche::h3::Error::Done)
                                | Err(quiche::h3::Error::StreamBlocked)
                                | Err(quiche::h3::Error::TransportError(
                                    quiche::Error::Done,
                                )) => break,
                                Err(e) => {
                                    return Err(
                                        format!("HTTP/3 send {:?}", e).into()
                                    )
                                },
                            }
                        }
                    }
                }
                loop {
                    match h.poll(c) {
                        Ok((id, quiche::h3::Event::Headers { list, .. })) => {
                            if !server {
                                let val = |key: &[u8]| {
                                    list.iter()
                                        .find(|x| x.name() == key)
                                        .map(|x| {
                                            String::from_utf8_lossy(x.value())
                                                .into_owned()
                                        })
                                        .unwrap_or_default()
                                };
                                let count = val(b"x-bytes");
                                let hash = val(b"x-sha256");
                                let mut expected = ring::digest::Context::new(
                                    &ring::digest::SHA256,
                                );
                                let mut left = amount;
                                while left > 0 {
                                    let n = left.min(body.len() as u64) as usize;
                                    expected.update(&body[..n]);
                                    left -= n as u64;
                                }
                                result_digest = hex(expected.finish().as_ref());
                                if val(b":status") != "200"
                                    || count != amount.to_string()
                                    || hash != result_digest
                                {
                                    return Err(format!(
                                        "HTTP/3 integrity failure {} {}",
                                        count, hash
                                    )
                                    .into());
                                }
                                verified = true;
                                c.close(true, 0, b"verified")?;
                            } else {
                                request = Some(id);
                            }
                        },
                        Ok((id, quiche::h3::Event::Data)) => loop {
                            match h.recv_body(c, id, &mut body) {
                                Ok(n) => {
                                    if body[..n].iter().any(|&v| v != 7) {
                                        return Err("body corruption".into());
                                    }
                                    received += n as u64;
                                    digest.update(&body[..n]);
                                },
                                Err(quiche::h3::Error::Done) => break,
                                Err(e) => {
                                    return Err(
                                        format!("HTTP/3 body {:?}", e).into()
                                    )
                                },
                            }
                        },
                        Ok((id, quiche::h3::Event::Finished)) => {
                            if server && !response {
                                if received != amount {
                                    return Err(format!(
                                        "short HTTP/3 upload {}",
                                        received
                                    )
                                    .into());
                                }
                                result_digest =
                                    hex(digest.clone().finish().as_ref());
                                let size = received.to_string();
                                let headers = [
                                    quiche::h3::Header::new(b":status", b"200"),
                                    quiche::h3::Header::new(
                                        b"content-length",
                                        b"0",
                                    ),
                                    quiche::h3::Header::new(
                                        b"x-bytes",
                                        size.as_bytes(),
                                    ),
                                    quiche::h3::Header::new(
                                        b"x-sha256",
                                        result_digest.as_bytes(),
                                    ),
                                ];
                                h.send_response(c, id, &headers, true)?;
                                response = true;
                                verified = true;
                            }
                        },
                        Ok((_id, quiche::h3::Event::Reset(e))) => {
                            return Err(format!("HTTP/3 reset {}", e).into())
                        },
                        Ok(_) => {},
                        Err(quiche::h3::Error::Done) => break,
                        Err(e) => {
                            return Err(format!("HTTP/3 poll {:?}", e).into())
                        },
                    }
                }
            }
            // Honor quiche's pacing release timestamp.
            loop {
                match c.send(&mut output) {
                    Ok((n, info)) => {
                        if info.at > Instant::now() {
                            std::thread::sleep(
                                info.at.saturating_duration_since(Instant::now()),
                            );
                        }
                        socket.send_to(&output[..n], info.to)?;
                    },
                    Err(quiche::Error::Done) => break,
                    Err(e) => return Err(format!("QUIC send {:?}", e).into()),
                }
            }
            if (!server && verified)
                || c.is_closed()
                || (server && c.is_draining())
            {
                if !verified {
                    return Err(
                        "connection closed before verified completion".into()
                    );
                }
                let elapsed = start.elapsed().as_secs_f64();
                emit(
                    &serde_json::json!({"role":if server{"receiver"}else{"sender"},"protocol":"h3",
                    "bytes":if server{received}else{sent},"verified":verified,"sha256":result_digest,
                    "seconds":elapsed,"transfer_seconds":request_start.map(|t|t.elapsed().as_secs_f64()),
                    "goodput_mbps":amount as f64*8.0/elapsed/1e6,"ack_packets":c.ack_packets_sent(),
                    "audit_heuristic_rollbacks":c.audit_cubic_rollbacks(),"lost_packets":c.stats().lost,"early_retransmissions":c.sidekick_retransmissions(),"sidekick":stats(c)}),
                );
                return Ok(());
            }
        }
        std::thread::sleep(Duration::from_micros(100));
    }
}
fn main() {
    if let Err(e) = run() {
        eprintln!("sidekick_transfer: {}", e);
        std::process::exit(1);
    }
}
