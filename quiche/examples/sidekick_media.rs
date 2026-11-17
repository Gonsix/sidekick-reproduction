// Independent encrypted UDP media: 240-byte frames / 20 ms, NACK retransmission.
mod sidekick_support;
use quiche::sidekick::{identifier, Error as FeedbackError, Sender};
use ring::aead::{self, LessSafeKey, Nonce, UnboundKey};
use ring::rand::{SecureRandom, SystemRandom};
use sidekick_support::*;
use std::collections::{BTreeMap, HashMap};
use std::convert::TryInto;
use std::net::UdpSocket;
use std::time::{Duration, Instant};

fn seal(key: &LessSafeKey, plain: &[u8]) -> Vec<u8> {
    let mut nonce = [0u8; 12];
    SystemRandom::new().fill(&mut nonce).unwrap();
    let mut data = plain.to_vec();
    key.seal_in_place_append_tag(
        Nonce::assume_unique_for_key(nonce),
        aead::Aad::empty(),
        &mut data,
    )
    .unwrap();
    let mut b = b"SKM1".to_vec();
    b.extend(nonce);
    b.extend(data);
    b
}
fn open(key: &LessSafeKey, b: &mut [u8]) -> Option<Vec<u8>> {
    if b.len() < 32 || &b[..4] != b"SKM1" {
        return None;
    }
    let nonce: [u8; 12] = b[4..16].try_into().ok()?;
    key.open_in_place(
        Nonce::assume_unique_for_key(nonce),
        aead::Aad::empty(),
        &mut b[16..],
    )
    .ok()
    .map(|x| x.to_vec())
}
fn transmit(
    socket: &UdpSocket, key: &LessSafeKey, core: &mut Option<Sender>, seq: u64,
    kind: u8,
) -> Result<()> {
    let mut plain = vec![kind];
    plain.extend(seq.to_be_bytes());
    if kind == 0 {
        plain.extend([7u8; 240]);
    }
    let b = seal(key, &plain);
    socket.send(&b)?;
    if let Some(s) = core.as_mut() {
        s.record(
            identifier(&b).unwrap(),
            if kind == 0 { seq } else { u64::MAX },
            Instant::now(),
        )
        .map_err(|e| format!("{:?}", e))?;
    }
    Ok(())
}
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
    let rawkey = a.token("media-key");
    let key = LessSafeKey::new(
        UnboundKey::new(&aead::AES_128_GCM, &rawkey).map_err(|_| "media key")?,
    );
    let socket = UdpSocket::bind(bind)?;
    socket.set_nonblocking(true)?;
    if !server {
        socket.connect(peer)?;
    }
    let mut feedback = if server { None } else { Feedback::new(&a)? };
    let mut core = if a.on("sidekick", false) && !server {
        Some(
            Sender::new(a.options(true), a.token("token"))
                .map_err(|e| format!("{:?}", e))?,
        )
    } else {
        None
    };
    let interval = Duration::from_millis(a.num("interval-ms", 20));
    if interval.is_zero() {
        return Err("zero media interval".into());
    }
    let count = a.num("seconds", 60) * 1000 / interval.as_millis() as u64;
    if count == 0 {
        return Err("zero media packets".into());
    }
    let rtt = Duration::from_millis(52);
    let timeout = Duration::from_secs(a.num("timeout", a.num("seconds", 60) + 8));
    let start = Instant::now();
    let mut seq = 0u64;
    let mut next_send = start;
    let mut next_end = start;
    let mut completed = false;
    let mut done_at = None;
    let mut next = 0u64;
    let mut high = None;
    let mut pending: BTreeMap<u64, u128> = BTreeMap::new();
    let mut played: Vec<(u64, u128, u128)> = vec![];
    let mut nack_time: HashMap<u64, Instant> = HashMap::new();
    let mut last_tx: HashMap<u64, Instant> = HashMap::new();
    let mut retrans_quack = 0u64;
    let mut retrans_nack = 0u64;
    let mut nacks = 0u64;
    let mut target = None;
    let mut b = [0u8; 65536];
    while start.elapsed() < timeout {
        let now = Instant::now();
        if !server && seq < count && now >= next_send {
            transmit(&socket, &key, &mut core, seq, 0)?;
            last_tx.insert(seq, now);
            seq += 1;
            next_send = start + interval.mul_f64(seq as f64);
        }
        if !server && seq == count && now >= next_end {
            transmit(&socket, &key, &mut core, count, 2)?;
            next_end = now + rtt;
        }
        if let (Some(f), Some(s)) = (feedback.as_mut(), core.as_mut()) {
            loop {
                match f.socket.recv(&mut b) {
                    Ok(n) => match s.feedback(&b[..n], Instant::now()) {
                        Ok(report) => {
                            f.renew()?;
                            if s.options.early_retransmit {
                                for id in report.lost {
                                    if id < seq
                                        && last_tx.get(&id).map_or(true, |t| {
                                            t.elapsed()
                                                >= Duration::from_millis(2)
                                        })
                                    {
                                        let mut plain = vec![0];
                                        plain.extend(id.to_be_bytes());
                                        plain.extend([7u8; 240]);
                                        let wire = seal(&key, &plain);
                                        socket.send(&wire)?;
                                        s.record(
                                            identifier(&wire).unwrap(),
                                            id,
                                            Instant::now(),
                                        )
                                        .map_err(|e| format!("{:?}", e))?;
                                        last_tx.insert(id, Instant::now());
                                        retrans_quack += 1;
                                    }
                                }
                            }
                        },
                        Err(FeedbackError::Overflow)
                        | Err(FeedbackError::Decode)
                        | Err(FeedbackError::Ambiguous)
                            if f.resets < 3 =>
                        {
                            s.reset();
                            f.generation = s.generation;
                            f.resets += 1;
                            f.configure()?;
                        },
                        Err(_) => {},
                    },
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(e) => return Err(e.into()),
                }
            }
            if f.heartbeat_elapsed() {
                f.renew()?;
            }
        }
        loop {
            match socket.recv_from(&mut b) {
                Ok((n, from)) => {
                    let plain = match open(&key, &mut b[..n]) {
                        Some(p) => p,
                        None => continue,
                    };
                    if plain.len() < 9 {
                        continue;
                    }
                    let id = u64::from_be_bytes(plain[1..9].try_into()?);
                    if server {
                        target = Some(from);
                        match plain[0] {
                            0 => {
                                if plain.len() != 249
                                    || plain[9..].iter().any(|&v| v != 7)
                                    || id >= count
                                {
                                    return Err("invalid media frame".into());
                                }
                                high = Some(high.map_or(id, |h: u64| h.max(id)));
                                if id >= next && !pending.contains_key(&id) {
                                    let receive_now = start.elapsed().as_nanos();
                                    pending.insert(id, receive_now);
                                    while let Some(arrival) =
                                        pending.remove(&next)
                                    {
                                        // A frame that fills the head is playable at this arrival instant.
                                        played.push((next, arrival, receive_now));
                                        next += 1;
                                    }
                                }
                            },
                            2 => {
                                if id != count {
                                    return Err("media count mismatch".into());
                                }
                                high = Some(count - 1);
                            },
                            _ => {},
                        }
                    } else if plain[0] == 1 {
                        if (plain.len() - 1) % 8 != 0 {
                            return Err("invalid NACK".into());
                        }
                        for bytes in plain[1..].chunks_exact(8) {
                            let id = u64::from_be_bytes(bytes.try_into()?);
                            if id < seq
                                && last_tx.get(&id).map_or(true, |t| {
                                    t.elapsed() >= Duration::from_millis(2)
                                })
                            {
                                transmit(&socket, &key, &mut core, id, 0)?;
                                last_tx.insert(id, Instant::now());
                                retrans_nack += 1;
                            }
                        }
                    } else if plain[0] == 3 && id == count {
                        completed = true;
                        break;
                    }
                },
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(e) => return Err(e.into()),
            }
        }
        if server {
            if let Some(to) = target {
                if let Some(h) = high {
                    let now = Instant::now();
                    let missing: Vec<u64> = (next..=h)
                        .filter(|i| {
                            !pending.contains_key(i)
                                && nack_time.get(i).map_or(true, |t| {
                                    now.saturating_duration_since(*t) >= rtt
                                })
                        })
                        .take(128)
                        .collect();
                    if !missing.is_empty() {
                        let mut plain = vec![1];
                        for id in missing {
                            plain.extend(id.to_be_bytes());
                            nack_time.insert(id, now);
                        }
                        socket.send_to(&seal(&key, &plain), to)?;
                        nacks += 1;
                    }
                }
                if next == count {
                    if done_at.is_none() {
                        done_at = Some(Instant::now());
                        next_end = Instant::now();
                    }
                    if Instant::now() >= next_end {
                        let mut plain = vec![3];
                        plain.extend(count.to_be_bytes());
                        socket.send_to(&seal(&key, &plain), to)?;
                        next_end = Instant::now() + rtt;
                    }
                    if done_at.unwrap().elapsed() > Duration::from_millis(500) {
                        completed = true;
                        break;
                    }
                }
            }
        }
        if completed {
            break;
        }
        std::thread::sleep(Duration::from_micros(100));
    }
    if !completed {
        return Err(format!("media timeout next={}, sent={}", next, seq).into());
    }
    if server {
        let path = a.get("csv", "media.csv");
        let mut out = String::from("seq,receive_ns,play_ns,jitter_ns\n");
        let mut jitter = vec![];
        for (id, recv, play) in &played {
            let j = play - recv;
            jitter.push(j as f64 / 1e6);
            out.push_str(&format!("{},{},{},{}\n", id, recv, play, j));
        }
        std::fs::write(path, out)?;
        jitter.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let q = |p: f64| {
            jitter[((p * jitter.len() as f64).floor() as usize)
                .min(jitter.len() - 1)]
        };
        emit(
            &serde_json::json!({"role":"receiver","protocol":"media","verified":true,"samples":played.len(),
            "p95_ms":q(0.95),"p99_ms":q(0.99),"p999_ms":q(0.999),"nack_packets":nacks}),
        );
    } else {
        let stats=core.as_ref().map(|s|serde_json::json!({"valid":s.stats.valid,"rejected":s.stats.rejected,
            "resets":s.stats.resets,"early_lost":s.stats.early_lost})).unwrap_or(serde_json::json!({"valid":0}));
        emit(
            &serde_json::json!({"role":"sender","protocol":"media","verified":true,"packets":seq,
            "retrans_quack":retrans_quack,"retrans_nack":retrans_nack,"sidekick":stats}),
        );
    }
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("sidekick_media: {}", e);
        std::process::exit(1);
    }
}
