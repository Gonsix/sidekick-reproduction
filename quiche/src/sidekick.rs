#![allow(missing_docs)] // Experimental API; behavioral contract is in reproduction/README.md.
                        // Independent implementation of the algorithms in Sidekick (NSDI 2024).
                        // This module does not use the authors' implementation.
use std::collections::HashMap;
use std::convert::TryInto;
use std::time::{Duration, Instant};

pub const PRIME: u64 = 4_294_967_291;
pub const MAX_THRESHOLD: usize = 200;
pub const MAX_PACKETS: usize = 100_000;

#[derive(Clone, Debug, Default)]
pub struct Options {
    pub enabled: bool,
    pub early_retransmit: bool,
    pub pacubic: bool,
    pub ack_reduction: bool,
    pub threshold: usize,
    pub reorder_packets: usize,
}
impl Options {
    pub fn validate(&self) -> Result<(), Error> {
        if self.enabled
            && (!(1..=MAX_THRESHOLD).contains(&self.threshold)
                || self.reorder_packets == 0)
        {
            return Err(Error::Configuration);
        }
        Ok(())
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    Configuration,
    Malformed,
    Session,
    Stale,
    Overflow,
    Ambiguous,
    Decode,
    Capacity,
}
#[derive(Clone, Debug)]
pub struct Sketch {
    pub count: u32,
    pub last: u32,
    pub sums: Vec<u32>,
}
fn add(a: u32, b: u32) -> u32 {
    ((a as u64 + b as u64) % PRIME) as u32
}
fn sub(a: u32, b: u32) -> u32 {
    ((a as u64 + PRIME - b as u64) % PRIME) as u32
}
fn mul(a: u32, b: u32) -> u32 {
    ((a as u64 * b as u64) % PRIME) as u32
}
fn pow(mut a: u32, mut n: u64) -> u32 {
    let mut y = 1;
    while n > 0 {
        if n & 1 != 0 {
            y = mul(y, a);
        }
        a = mul(a, a);
        n >>= 1;
    }
    y
}
pub fn identifier(payload: &[u8]) -> Option<u32> {
    if payload.len() < 4 {
        return None;
    }
    Some(
        (u32::from_be_bytes(payload[payload.len() - 4..].try_into().ok()?) as u64
            % PRIME) as u32,
    )
}
impl Sketch {
    pub fn new(threshold: usize) -> Result<Self, Error> {
        if !(1..=MAX_THRESHOLD).contains(&threshold) {
            return Err(Error::Configuration);
        }
        Ok(Self {
            count: 0,
            last: 0,
            sums: vec![0; threshold],
        })
    }
    pub fn insert(&mut self, id: u32) -> Result<(), Error> {
        self.count = self.count.checked_add(1).ok_or(Error::Capacity)?;
        let mut p = id;
        for s in &mut self.sums {
            *s = add(*s, p);
            p = mul(p, id);
        }
        self.last = id;
        Ok(())
    }
    pub fn encode(
        &self, token: &[u8; 16], generation: u32, delay_us: u32,
    ) -> Vec<u8> {
        let mut b = b"SKQ1".to_vec();
        b.extend(token);
        b.extend(generation.to_be_bytes());
        b.extend(self.count.to_be_bytes());
        b.extend(self.last.to_be_bytes());
        b.extend(delay_us.to_be_bytes());
        for s in &self.sums {
            b.extend(s.to_be_bytes());
        }
        b
    }
    pub fn parse(
        b: &[u8], token: &[u8; 16], generation: u32, t: usize,
    ) -> Result<(Self, u32), Error> {
        if b.len() != 36 + 4 * t || &b[..4] != b"SKQ1" {
            return Err(Error::Malformed);
        }
        if &b[4..20] != token
            || u32::from_be_bytes(b[20..24].try_into().unwrap()) != generation
        {
            return Err(Error::Session);
        }
        let u = |i| u32::from_be_bytes(b[i..i + 4].try_into().unwrap());
        let sums: Vec<u32> = (0..t).map(|i| u(36 + 4 * i)).collect();
        if sums.iter().any(|&s| s as u64 >= PRIME) || u(28) as u64 >= PRIME {
            return Err(Error::Malformed);
        }
        Ok((
            Self {
                count: u(24),
                last: u(28),
                sums,
            },
            u(32),
        ))
    }
}
// Newton identities reconstruct the monic polynomial whose roots are missing IDs.
// All transmitted power sums are verified after candidate evaluation.
pub fn decode_missing(
    sums: &[u32], missing: usize, candidates: &[u32],
) -> Result<Vec<u32>, Error> {
    if missing > sums.len() {
        return Err(Error::Overflow);
    }
    if missing == 0 {
        return if sums.iter().all(|&v| v == 0) {
            Ok(vec![])
        } else {
            Err(Error::Decode)
        };
    }
    let mut e = vec![0u32; missing + 1];
    e[0] = 1;
    for k in 1..=missing {
        let mut v = 0;
        for i in 1..=k {
            let z = mul(e[k - i], sums[i - 1]);
            v = if i % 2 == 1 { add(v, z) } else { sub(v, z) };
        }
        e[k] = mul(v, pow(k as u32, PRIME - 2));
    }
    let mut seen = std::collections::HashSet::new();
    if candidates.iter().any(|x| !seen.insert(*x)) {
        return Err(Error::Ambiguous);
    }
    let roots: Vec<u32> = candidates
        .iter()
        .copied()
        .filter(|&x| {
            let mut v = 1;
            for k in 1..=missing {
                v = mul(v, x);
                v = if k % 2 == 1 {
                    sub(v, e[k])
                } else {
                    add(v, e[k])
                };
            }
            v == 0
        })
        .collect();
    if roots.len() != missing {
        return Err(Error::Decode);
    }
    let mut check = Sketch::new(sums.len())?;
    for &x in &roots {
        check.insert(x)?;
    }
    if check.sums != sums {
        return Err(Error::Decode);
    }
    Ok(roots)
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Fate {
    Unknown,
    Received,
    Lost,
}
struct Entry {
    id: u32,
    pn: u64,
    sent: Instant,
    prefix: Sketch,
    fate: Fate,
}
#[derive(Clone, Debug, Default)]
pub struct Report {
    pub received: Vec<u64>,
    pub lost: Vec<u64>,
    pub near_rtt: Option<Duration>,
}
#[derive(Clone, Debug, Default)]
pub struct Stats {
    pub valid: u64,
    pub rejected: u64,
    pub resets: u64,
    pub early_lost: u64,
    pub proxy_received: u64,
}
pub struct Sender {
    pub options: Options,
    pub token: [u8; 16],
    pub generation: u32,
    sent: Sketch,
    excluded: Sketch,
    ledger: Vec<Entry>,
    latest: HashMap<u32, usize>,
    front: usize,
    last_position: usize,
    last_count: u32,
    pub stats: Stats,
    pub near_rtt: Option<Duration>,
}
impl Sender {
    pub fn new(options: Options, token: [u8; 16]) -> Result<Self, Error> {
        options.validate()?;
        let t = options.threshold.max(1);
        Ok(Self {
            options,
            token,
            generation: 0,
            sent: Sketch::new(t)?,
            excluded: Sketch::new(t)?,
            ledger: vec![],
            latest: HashMap::new(),
            front: 0,
            last_position: 0,
            last_count: 0,
            stats: Stats::default(),
            near_rtt: None,
        })
    }
    pub fn reset(&mut self) {
        self.generation = self.generation.wrapping_add(1);
        self.sent = Sketch::new(self.options.threshold.max(1)).unwrap();
        self.excluded = self.sent.clone();
        self.ledger.clear();
        self.latest.clear();
        self.front = 0;
        self.last_position = 0;
        self.last_count = 0;
        self.stats.resets += 1;
    }
    pub fn record(
        &mut self, id: u32, pn: u64, now: Instant,
    ) -> Result<(), Error> {
        if !self.options.enabled {
            return Ok(());
        }
        if self.ledger.len() >= MAX_PACKETS {
            return Err(Error::Capacity);
        }
        self.sent.insert(id)?;
        self.latest.insert(id, self.ledger.len());
        self.ledger.push(Entry {
            id,
            pn,
            sent: now,
            prefix: self.sent.clone(),
            fate: Fate::Unknown,
        });
        Ok(())
    }
    pub fn feedback(
        &mut self, bytes: &[u8], now: Instant,
    ) -> Result<Report, Error> {
        let result = self.decode(bytes, now);
        match &result {
            Ok(r) => {
                self.stats.valid += 1;
                if self.options.early_retransmit {
                    self.stats.early_lost += r.lost.len() as u64;
                }
                self.stats.proxy_received += r.received.len() as u64;
            },
            Err(_) => self.stats.rejected += 1,
        }
        result
    }
    fn decode(&mut self, bytes: &[u8], now: Instant) -> Result<Report, Error> {
        if !self.options.enabled {
            return Err(Error::Configuration);
        }
        let (s, delay_us) = Sketch::parse(
            bytes,
            &self.token,
            self.generation,
            self.sent.sums.len(),
        )?;
        if s.count <= self.last_count {
            return Err(Error::Stale);
        }
        let pos = *self.latest.get(&s.last).ok_or(Error::Ambiguous)?;
        if pos < self.last_position {
            return Err(Error::Stale);
        }
        let prefix = &self.ledger[pos].prefix;
        let n = prefix
            .count
            .checked_sub(self.excluded.count)
            .ok_or(Error::Decode)?;
        let m = n.checked_sub(s.count).ok_or(Error::Decode)? as usize;
        if m > self.sent.sums.len() {
            return Err(Error::Overflow);
        }
        let sums: Vec<u32> = prefix
            .sums
            .iter()
            .zip(&self.excluded.sums)
            .zip(&s.sums)
            .map(|((&a, &b), &c)| sub(sub(a, b), c))
            .collect();
        let indices: Vec<usize> = (self.front..=pos)
            .filter(|&i| self.ledger[i].fate == Fate::Unknown)
            .collect();
        let ids: Vec<u32> = indices.iter().map(|&i| self.ledger[i].id).collect();
        let roots = decode_missing(&sums, m, &ids)?;
        let missing: std::collections::HashSet<u32> = roots.into_iter().collect();
        let received_indices: Vec<usize> = indices
            .iter()
            .copied()
            .filter(|&i| !missing.contains(&self.ledger[i].id))
            .collect();
        let mut r = Report::default();
        // Count received successors, including already resolved ones.
        let mut after = 0;
        for i in (self.front..=pos).rev() {
            let e = &mut self.ledger[i];
            if e.fate == Fate::Received {
                after += 1;
            } else if e.fate == Fate::Unknown {
                if missing.contains(&e.id) {
                    if after >= self.options.reorder_packets {
                        e.fate = Fate::Lost;
                        self.excluded.insert(e.id)?;
                        r.lost.push(e.pn);
                    }
                } else {
                    e.fate = Fate::Received;
                    after += 1;
                }
            }
        }
        r.received = received_indices
            .iter()
            .map(|&i| self.ledger[i].pn)
            .collect();
        // The feedback reports time spent at the proxy after observing its last ID.
        let sample = now
            .saturating_duration_since(self.ledger[pos].sent)
            .saturating_sub(Duration::from_micros(delay_us as u64));
        if !sample.is_zero() {
            let sample = sample.max(Duration::from_micros(100));
            self.near_rtt =
                Some(self.near_rtt.map_or(sample, |old| old.min(sample)));
        }
        r.near_rtt = self.near_rtt;
        self.last_position = pos;
        self.last_count = s.count;
        while self.front < self.ledger.len()
            && self.ledger[self.front].fate != Fate::Unknown
        {
            self.front += 1;
        }
        Ok(r)
    }
}
/// PACUBIC constants, equation in section 4.3.2.
pub fn pacubic_parameters(ratio: f64) -> (f64, f64) {
    let r = ratio.clamp(0.01, 1.0);
    (1.0 - r * (1.0 - 0.7), 0.4 / r.powi(3))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn options(t: usize) -> Options {
        Options {
            enabled: true,
            early_retransmit: true,
            threshold: t,
            reorder_packets: 3,
            ..Default::default()
        }
    }
    #[test]
    fn recover_and_corruption() {
        for m in 0..=8 {
            let mut s = Sketch::new(8).unwrap();
            let ids: Vec<u32> = (1..=40).map(|x| x * 7919).collect();
            for x in &ids[..m] {
                s.insert(*x).unwrap();
            }
            assert_eq!(decode_missing(&s.sums, m, &ids).unwrap(), ids[..m]);
            s.sums[7] = add(s.sums[7], 1);
            assert!(decode_missing(&s.sums, m, &ids).is_err());
        }
    }
    #[test]
    fn cumulative_lost_feedback_and_duplicates() {
        let token = [7; 16];
        let now = Instant::now();
        let mut s = Sender::new(options(4), token).unwrap();
        let mut p = Sketch::new(4).unwrap();
        for i in 1..=5 {
            s.record(i, i as u64, now).unwrap();
            if i != 2 {
                p.insert(i).unwrap();
            }
        }
        let b = p.encode(&token, 0, 0);
        let r = s.feedback(&b, now + Duration::from_millis(2)).unwrap();
        assert_eq!(r.lost, vec![2]);
        assert_eq!(r.received, vec![1, 3, 4, 5]);
        assert_eq!(s.feedback(&b, now).unwrap_err(), Error::Stale);
        for i in 6..=10 {
            s.record(i, i as u64, now).unwrap();
            if i != 6 {
                p.insert(i).unwrap();
            }
        }
        assert_eq!(
            s.feedback(&p.encode(&token, 0, 0), now).unwrap().lost,
            vec![6]
        );
    }
    #[test]
    fn reorder_then_deliver() {
        let now = Instant::now();
        let token = [2; 16];
        let mut s = Sender::new(options(3), token).unwrap();
        let mut p = Sketch::new(3).unwrap();
        for i in 1..=3 {
            s.record(i, i as u64, now).unwrap();
        }
        for i in [1, 3] {
            p.insert(i).unwrap();
        }
        assert!(s
            .feedback(&p.encode(&token, 0, 0), now)
            .unwrap()
            .lost
            .is_empty());
        p.insert(2).unwrap();
        assert_eq!(
            s.feedback(&p.encode(&token, 0, 0), now).unwrap_err(),
            Error::Stale
        );
        // A last-ID that goes backwards is intentionally rejected. A later
        // cumulative report recovers the reordered packet without a false loss.
        s.record(4, 4, now).unwrap();
        p.insert(4).unwrap();
        assert!(s
            .feedback(&p.encode(&token, 0, 0), now)
            .unwrap()
            .lost
            .is_empty());
    }
    #[test]
    fn overflow_session_collision_disable() {
        let now = Instant::now();
        let token = [3; 16];
        let mut s = Sender::new(options(1), token).unwrap();
        let mut p = Sketch::new(1).unwrap();
        for i in 1..=5 {
            s.record(i, i as u64, now).unwrap();
        }
        p.insert(5).unwrap();
        let b = p.encode(&token, 0, 0);
        assert_eq!(s.feedback(&b, now).unwrap_err(), Error::Overflow);
        let mut b = b;
        b[4] ^= 1;
        assert_eq!(s.feedback(&b, now).unwrap_err(), Error::Session);
        assert!(decode_missing(&[1], 1, &[1, 1]).is_err());
        let mut o = options(1);
        o.enabled = false;
        let mut off = Sender::new(o, token).unwrap();
        off.record(1, 1, now).unwrap();
        assert!(off.ledger.is_empty());
    }
    #[test]
    fn wire_length_and_pacubic() {
        let p = Sketch::new(10).unwrap();
        let b = p.encode(&[0; 16], 3, 17);
        assert_eq!(b.len(), 76);
        assert!(Sketch::parse(&b[..b.len() - 1], &[0; 16], 3, 10).is_err());
        let (beta, c) = pacubic_parameters(2.0 / 52.0);
        assert!((beta - (1.0 - (2.0 / 52.0) * 0.3)).abs() < 1e-10);
        assert!((c - 0.4 / (2.0f64 / 52.0).powi(3)).abs() < 1e-8);
    }
}

#[cfg(test)]
mod extra_sidekick_tests {
    use super::*;
    #[test]
    fn sidekick_randomized_reconciliation() {
        let mut seed = 987654321u64;
        for trial in 0..128 {
            let m = trial % 12;
            let mut ids = vec![];
            while ids.len() < 100 {
                seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
                let x = (seed % PRIME) as u32;
                if !ids.contains(&x) {
                    ids.push(x);
                }
            }
            let mut sketch = Sketch::new(16).unwrap();
            for &x in &ids[..m] {
                sketch.insert(x).unwrap();
            }
            let mut got = decode_missing(&sketch.sums, m, &ids).unwrap();
            got.sort_unstable();
            let mut want = ids[..m].to_vec();
            want.sort_unstable();
            assert_eq!(got, want);
        }
    }
    #[test]
    fn sidekick_epoch_reset_rejects_old_feedback() {
        let options = Options {
            enabled: true,
            threshold: 4,
            reorder_packets: 3,
            ..Default::default()
        };
        let mut s = Sender::new(options, [1; 16]).unwrap();
        let mut p = Sketch::new(4).unwrap();
        let now = Instant::now();
        s.record(9, 1, now).unwrap();
        p.insert(9).unwrap();
        let b = p.encode(&[1; 16], 0, 0);
        s.reset();
        assert_eq!(s.feedback(&b, now).unwrap_err(), Error::Session);
        s.record(4, 2, now).unwrap();
        let mut p = Sketch::new(4).unwrap();
        p.insert(4).unwrap();
        assert_eq!(
            s.feedback(&p.encode(&[1; 16], 1, 0), now).unwrap().received,
            vec![2]
        );
    }
}

#[cfg(test)]
mod api_switch_tests {
    use super::*;
    #[test]
    fn sidekick_master_off_preserves_live_quic_connection() {
        let mut config = crate::Config::new(crate::PROTOCOL_VERSION).unwrap();
        let local = "127.0.0.1:44330".parse().unwrap();
        let peer = "127.0.0.1:4433".parse().unwrap();
        let scid = crate::ConnectionId::from_ref(&[8;16]);
        let mut conn = crate::connect(Some("localhost"),&scid,local,peer,&mut config).unwrap();
        let options = Options {enabled:true,early_retransmit:true,pacubic:true,
            ack_reduction:true,threshold:8,reorder_packets:3};
        conn.configure_sidekick(options.clone(),[2;16]).unwrap();
        assert!(conn.sidekick.is_some());
        let trace = conn.trace_id().to_owned();
        let mut off = options;
        off.enabled = false;
        conn.configure_sidekick(off,[2;16]).unwrap();
        assert!(conn.sidekick.is_none());
        assert!(!conn.is_closed());
        assert_eq!(conn.trace_id(),trace);
    }
}
