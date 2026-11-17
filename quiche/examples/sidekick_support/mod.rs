#![allow(dead_code)] // This helper is shared by three independent example binaries.
use quiche::sidekick::{self, Options};
use ring::rand::{SecureRandom, SystemRandom};
use std::collections::HashMap;
use std::convert::TryInto;
use std::net::{SocketAddr, UdpSocket};
use std::time::{Duration, Instant};
pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub struct Args {
    pub values: HashMap<String, String>,
}
impl Args {
    pub fn new() -> Self {
        let mut values: HashMap<String, String> = HashMap::new();
        let mut it = std::env::args().skip(1);
        while let Some(k) = it.next() {
            assert!(k.starts_with("--"), "expected --option, got {}", k);
            let v = it.next().expect("every option needs a value");
            assert!(
                values.insert(k[2..].into(), v).is_none(),
                "duplicate option {}",
                k
            );
        }
        let allowed = [
            "role",
            "bind",
            "peer",
            "bytes",
            "timeout",
            "cert",
            "key",
            "sidekick",
            "early-retransmit",
            "pacubic",
            "ack-reduction",
            "ack-delay-ms",
            "threshold",
            "quack-ms",
            "quack-packets",
            "feedback",
            "proxy",
            "token",
            "iface",
            "sender-ip",
            "receiver-ip",
            "sender-port",
            "receiver-port",
            "seconds",
            "interval-ms",
            "media-key",
            "csv",
        ];
        for k in values.keys() {
            assert!(allowed.contains(&k.as_str()), "unknown option {}", k);
        }
        Self { values }
    }
    pub fn get(&self, k: &str, d: &str) -> String {
        self.values.get(k).cloned().unwrap_or_else(|| d.into())
    }
    pub fn num(&self, k: &str, d: u64) -> u64 {
        self.get(k, &d.to_string()).parse().expect("invalid number")
    }
    pub fn on(&self, k: &str, d: bool) -> bool {
        match self.get(k, if d { "on" } else { "off" }).as_str() {
            "on" => true,
            "off" => false,
            _ => panic!("{} must be on/off", k),
        }
    }
    pub fn token(&self, k: &str) -> [u8; 16] {
        let s = self.get(k, "");
        assert_eq!(s.len(), 32, "{} needs 32 hex characters", k);
        let mut b = [0; 16];
        for i in 0..16 {
            b[i] = u8::from_str_radix(&s[2 * i..2 * i + 2], 16)
                .expect("invalid hex");
        }
        b
    }
    pub fn options(&self, media: bool) -> Options {
        Options {
            enabled: self.on("sidekick", false),
            early_retransmit: self.on("early-retransmit", false),
            pacubic: self.on("pacubic", false),
            ack_reduction: self.on("ack-reduction", false),
            threshold: self.num("threshold", 10) as usize,
            reorder_packets: if media { 1 } else { 3 },
        }
    }
}
pub fn random16() -> [u8; 16] {
    let mut b = [0; 16];
    SystemRandom::new().fill(&mut b).unwrap();
    b
}
pub fn hex(b: &[u8]) -> String {
    b.iter().map(|v| format!("{:02x}", v)).collect()
}
pub fn stats(conn: &quiche::Connection) -> serde_json::Value {
    if let Some(s) = conn.sidekick_stats() {
        serde_json::json!({"valid":s.valid,"rejected":s.rejected,
        "resets":s.resets,"early_lost":s.early_lost,"proxy_received":s.proxy_received})
    } else {
        serde_json::json!({"valid":0,"rejected":0,"resets":0,"early_lost":0,"proxy_received":0})
    }
}
pub struct Feedback {
    pub socket: UdpSocket,
    pub token: [u8; 16],
    pub generation: u32,
    pub threshold: u16,
    pub packets: u16,
    pub interval: u32,
    heartbeat: Instant,
    pub resets: u32,
}
impl Feedback {
    pub fn new(a: &Args) -> Result<Option<Self>> {
        if !a.on("sidekick", false) {
            return Ok(None);
        }
        let socket = UdpSocket::bind(a.get("feedback", "0.0.0.0:5103"))?;
        socket.connect(a.get("proxy", "127.0.0.1:9000"))?;
        let mut f = Self {
            socket,
            token: a.token("token"),
            generation: 0,
            threshold: a.num("threshold", 10).try_into()?,
            packets: a.num("quack-packets", 0).try_into()?,
            interval: a.num("quack-ms", 30).try_into()?,
            heartbeat: Instant::now(),
            resets: 0,
        };
        f.socket
            .set_read_timeout(Some(Duration::from_millis(100)))?;
        let start = Instant::now();
        let mut b = [0; 4096];
        loop {
            f.configure()?;
            if let Ok(n) = f.socket.recv(&mut b) {
                if n == 24
                    && &b[..4] == b"SKA1"
                    && b[4..20] == f.token
                    && u32::from_be_bytes(b[20..24].try_into().unwrap()) == 0
                {
                    break;
                }
            }
            if start.elapsed() > Duration::from_secs(3) {
                return Err("Robin proxy configuration timed out".into());
            }
        }
        f.socket.set_read_timeout(None)?;
        f.socket.set_nonblocking(true)?;
        Ok(Some(f))
    }
    pub fn heartbeat_elapsed(&self) -> bool {
        self.heartbeat.elapsed() > Duration::from_millis(100)
    }
    pub fn configure(&mut self) -> Result<()> {
        let mut b = b"SKC1".to_vec();
        b.extend(self.token);
        b.extend(self.generation.to_be_bytes());
        b.extend(self.threshold.to_be_bytes());
        b.extend(self.packets.to_be_bytes());
        b.extend(self.interval.to_be_bytes());
        b.extend(128u32.to_be_bytes());
        self.socket.send(&b)?;
        self.heartbeat = Instant::now();
        Ok(())
    }
    pub fn renew(&mut self) -> Result<()> {
        let mut b = b"SKR1".to_vec();
        b.extend(self.token);
        b.extend(self.generation.to_be_bytes());
        b.extend(128u32.to_be_bytes());
        self.socket.send(&b)?;
        self.heartbeat = Instant::now();
        Ok(())
    }
    pub fn quic(&mut self, conn: &mut quiche::Connection) -> Result<()> {
        let mut b = [0; 4096];
        loop {
            match self.socket.recv(&mut b) {
                Ok(n) => match conn.sidekick_feedback(&b[..n]) {
                    Ok(_) => {
                        self.renew()?;
                    },
                    Err(sidekick::Error::Overflow)
                    | Err(sidekick::Error::Decode)
                    | Err(sidekick::Error::Ambiguous)
                        if self.resets < 3 =>
                    {
                        self.generation = conn.reset_sidekick().unwrap();
                        self.resets += 1;
                        self.configure()?;
                    },
                    Err(_) => {},
                },
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(e) => return Err(e.into()),
            }
        }
        if self.heartbeat.elapsed() > Duration::from_millis(100) {
            self.renew()?;
        }
        Ok(())
    }
}
pub fn emit(value: &serde_json::Value) {
    println!("{}", value);
}
pub fn address(a: &Args, key: &str, default: &str) -> SocketAddr {
    a.get(key, default).parse().expect("invalid socket address")
}
