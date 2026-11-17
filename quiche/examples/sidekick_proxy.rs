// Packet observer: never terminates TLS, parses QUIC packet numbers, or forwards data.
mod sidekick_support;
use quiche::sidekick::Sketch;
use sidekick_support::*;
use std::convert::TryInto;
use std::net::UdpSocket;
use std::time::{Duration, Instant};
struct Raw(i32);
impl Drop for Raw {
    fn drop(&mut self) {
        unsafe {
            libc::close(self.0);
        }
    }
}
fn run() -> Result<()> {
    let a = Args::new();
    let token = a.token("token");
    let iface = std::ffi::CString::new(a.get("iface", "lo"))?;
    let fd = unsafe {
        libc::socket(
            libc::AF_PACKET,
            libc::SOCK_RAW | libc::SOCK_NONBLOCK,
            (0x0800u16.to_be()) as i32,
        )
    };
    if fd < 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let raw = Raw(fd);
    let mut addr: libc::sockaddr_ll = unsafe { std::mem::zeroed() };
    addr.sll_family = libc::AF_PACKET as u16;
    addr.sll_protocol = 0x0800u16.to_be();
    addr.sll_ifindex = unsafe { libc::if_nametoindex(iface.as_ptr()) } as i32;
    if addr.sll_ifindex == 0 {
        return Err("unknown observer interface".into());
    }
    if unsafe {
        libc::bind(
            raw.0,
            &addr as *const _ as *const libc::sockaddr,
            std::mem::size_of_val(&addr) as u32,
        )
    } < 0
    {
        return Err(std::io::Error::last_os_error().into());
    }
    let socket = UdpSocket::bind(a.get("bind", "0.0.0.0:9000"))?;
    socket.set_nonblocking(true)?;
    let src: std::net::Ipv4Addr = a.get("sender-ip", "10.200.1.2").parse()?;
    let dst: std::net::Ipv4Addr = a.get("receiver-ip", "10.200.2.2").parse()?;
    let sport = a.num("sender-port", 44330) as u16;
    let dport = a.num("receiver-port", 4433) as u16;
    let mut state: Option<Sketch> = None;
    let mut generation = None;
    let mut target = None;
    let mut interval = Duration::from_millis(30);
    let mut every = 0u32;
    let mut quota = 0u32;
    let mut reported = 0;
    let mut last_report = Instant::now();
    let mut last_packet = Instant::now();
    let mut observed = 0u64;
    let mut quacks = 0u64;
    let mut quack_bytes = 0u64;
    let mut configs = 0u64;
    let start = Instant::now();
    let deadline = Duration::from_secs(a.num("timeout", 180));
    let mut buf = [0u8; 65536];
    let mut control = [0u8; 4096];
    let mut stop = false;
    while !stop && start.elapsed() < deadline {
        loop {
            match socket.recv_from(&mut control) {
                Ok((n, from)) => {
                    if n < 20
                        || control[4..20] != token
                        || from.ip() != std::net::IpAddr::V4(src)
                    {
                        continue;
                    }
                    if &control[..4] == b"SKX1" {
                        stop = true;
                        break;
                    }
                    if n < 24 {
                        continue;
                    }
                    let gen = u32::from_be_bytes(control[20..24].try_into()?);
                    if &control[..4] == b"SKC1" && n == 36 {
                        let t = u16::from_be_bytes(control[24..26].try_into()?)
                            as usize;
                        let p = u16::from_be_bytes(control[26..28].try_into()?)
                            as u32;
                        let ms = u32::from_be_bytes(control[28..32].try_into()?);
                        if t == 0 || t > 200 || (p == 0 && ms == 0) || ms > 10_000
                        {
                            continue;
                        }
                        if generation.map_or(true, |old| gen > old) {
                            state = Some(
                                Sketch::new(t).map_err(|e| format!("{:?}", e))?,
                            );
                            generation = Some(gen);
                            target = Some(from);
                            reported = 0;
                            every = p;
                            interval = Duration::from_millis(ms as u64);
                            last_report = Instant::now();
                            configs += 1;
                        }
                        if generation == Some(gen) && target == Some(from) {
                            quota =
                                u32::from_be_bytes(control[32..36].try_into()?)
                                    .min(128);
                            let mut b = b"SKA1".to_vec();
                            b.extend(token);
                            b.extend(gen.to_be_bytes());
                            socket.send_to(&b, from)?;
                        }
                    } else if &control[..4] == b"SKR1"
                        && n == 28
                        && generation == Some(gen)
                        && target == Some(from)
                    {
                        quota = u32::from_be_bytes(control[24..28].try_into()?)
                            .min(128);
                    }
                },
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(e) => return Err(e.into()),
            }
        }
        // Limit batches so a saturated interface cannot starve feedback timers.
        for _ in 0..64 {
            let n = unsafe {
                libc::recv(raw.0, buf.as_mut_ptr() as *mut _, buf.len(), 0)
            };
            if n < 0 {
                let e = std::io::Error::last_os_error();
                if e.kind() == std::io::ErrorKind::WouldBlock {
                    break;
                }
                return Err(e.into());
            }
            let n = n as usize;
            if n < 42
                || buf[12..14] != [8, 0]
                || buf[14] >> 4 != 4
                || buf[23] != 17
            {
                continue;
            }
            let ihl = ((buf[14] & 15) as usize) * 4;
            if ihl < 20
                || n < 14 + ihl + 8
                || u16::from_be_bytes(buf[20..22].try_into()?) & 0x3fff != 0
            {
                continue;
            }
            if buf[26..30] != src.octets() || buf[30..34] != dst.octets() {
                continue;
            }
            let u = 14 + ihl;
            if u16::from_be_bytes(buf[u..u + 2].try_into()?) != sport
                || u16::from_be_bytes(buf[u + 2..u + 4].try_into()?) != dport
            {
                continue;
            }
            let len = u16::from_be_bytes(buf[u + 4..u + 6].try_into()?) as usize;
            if len < 12 || u + len > n {
                continue;
            }
            let payload = &buf[u + 8..u + len];
            if payload[0] & 0x80 != 0 {
                continue;
            } // only opaque 1-RTT or media payloads
            if let Some(s) = state.as_mut() {
                s.insert(quiche::sidekick::identifier(payload).unwrap())
                    .map_err(|e| format!("{:?}", e))?;
                observed += 1;
                last_packet = Instant::now();
                if every > 0 && s.count - reported >= every && quota > 0 {
                    let b = s.encode(&token, generation.unwrap(), 0);
                    socket.send_to(&b, target.unwrap())?;
                    reported = s.count;
                    quota -= 1;
                    quacks += 1;
                    quack_bytes += b.len() as u64;
                    last_report = Instant::now();
                }
            }
        }
        if let Some(s) = state.as_ref() {
            if every == 0
                && s.count > reported
                && last_report.elapsed() >= interval
                && quota > 0
            {
                let delay =
                    last_packet.elapsed().as_micros().min(u32::MAX as u128)
                        as u32;
                let b = s.encode(&token, generation.unwrap(), delay);
                socket.send_to(&b, target.unwrap())?;
                reported = s.count;
                quota -= 1;
                quacks += 1;
                quack_bytes += b.len() as u64;
                last_report = Instant::now();
            }
        }
        std::thread::sleep(Duration::from_micros(100));
    }
    emit(
        &serde_json::json!({"role":"proxy","observed":observed,"quacks":quacks,"payload_bytes":quack_bytes,"configurations":configs}),
    );
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("sidekick_proxy: {}", e);
        std::process::exit(1);
    }
}
