//! QR codes drawn by Bibo itself, without a new dependency: the messaging step shows the
//! pairing code WhatsApp gives Hermes as a square the phone scans. Byte mode, versions 1 to
//! 40, error correction level M (15 % of the code can be damaged and still read), the
//! smallest version that fits and the mask the standard's penalty rules pick (ISO/IEC 18004).
//! Checked module for module against two independent encoders (Project Nayuki's qrcodegen
//! and segno) and read back by macOS's own barcode reader, outside the repo.

/// Error correction codewords per block and number of blocks at level M, by version (index
/// 0 unused).
const ECC_PER_BLOCK: [usize; 41] = [
    0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28,
    28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
];
const BLOCKS: [usize; 41] = [
    0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21,
    23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49,
];

/// A finished code: `size` x `size` modules, row by row, true for dark.
pub struct Qr {
    size: usize,
    dark: Vec<bool>,
    /// Modules the standard fixes (finders, timing, alignment, format, version): data and
    /// masks never touch them.
    fixed: Vec<bool>,
}

/// The QR code of `data`, or None past the 2331 bytes the largest code holds.
pub fn encode(data: &[u8]) -> Option<Qr> {
    let count_bits = |ver: usize| if ver < 10 { 8 } else { 16 };
    let ver = (1..=40).find(|&v| 4 + count_bits(v) + 8 * data.len() <= data_codewords(v) * 8)?;
    let capacity = data_codewords(ver) * 8;
    let mut bits = Vec::with_capacity(capacity);
    push_bits(&mut bits, 0b0100, 4);
    push_bits(&mut bits, data.len(), count_bits(ver));
    for &byte in data {
        push_bits(&mut bits, byte.into(), 8);
    }
    let terminator = (capacity - bits.len()).min(4);
    push_bits(&mut bits, 0, terminator);
    let to_byte = (8 - bits.len() % 8) % 8;
    push_bits(&mut bits, 0, to_byte);
    for pad in [0xEC, 0x11].into_iter().cycle() {
        if bits.len() >= capacity {
            break;
        }
        push_bits(&mut bits, pad, 8);
    }
    let words: Vec<u8> = bits
        .chunks(8)
        .map(|byte| byte.iter().fold(0, |acc, &b| (acc << 1) | u8::from(b)))
        .collect();

    let size = ver * 4 + 17;
    let mut qr = Qr { size, dark: vec![false; size * size], fixed: vec![false; size * size] };
    qr.draw_fixed(ver);
    qr.draw_codewords(&interleave(ver, &words));
    // Masks are XOR, so applying one twice undoes it. Ties keep the lowest mask.
    let mask = (0..8)
        .min_by_key(|&mask| {
            qr.apply_mask(mask);
            qr.draw_format(mask);
            let score = qr.penalty();
            qr.apply_mask(mask);
            score
        })
        .unwrap_or(0);
    qr.apply_mask(mask);
    qr.draw_format(mask);
    Some(qr)
}

impl Qr {
    /// The code as SVG: black modules on white with the standard four-module quiet zone, so
    /// it scans on a dark screen too.
    pub fn to_svg(&self) -> String {
        let full = self.size + 8;
        let mut path = String::new();
        for y in 0..self.size {
            for x in 0..self.size {
                if self.dark[y * self.size + x] {
                    path += &format!("M{},{}h1v1h-1z", x + 4, y + 4);
                }
            }
        }
        format!(
            r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {full} {full}" shape-rendering="crispEdges"><rect width="{full}" height="{full}" fill="white"/><path d="{path}" fill="black"/></svg>"#
        )
    }

    fn set_fixed(&mut self, x: usize, y: usize, dark: bool) {
        let at = y * self.size + x;
        self.dark[at] = dark;
        self.fixed[at] = true;
    }

    fn draw_fixed(&mut self, ver: usize) {
        let size = self.size;
        for i in 0..size {
            self.set_fixed(6, i, i % 2 == 0);
            self.set_fixed(i, 6, i % 2 == 0);
        }
        for (x, y) in [(3, 3), (size - 4, 3), (3, size - 4)] {
            self.draw_finder(x, y);
        }
        let positions = alignment_positions(ver);
        let last = positions.len().saturating_sub(1);
        for (i, &x) in positions.iter().enumerate() {
            for (j, &y) in positions.iter().enumerate() {
                let on_finder = (i, j) == (0, 0) || (i, j) == (0, last) || (i, j) == (last, 0);
                if !on_finder {
                    self.draw_alignment(x, y);
                }
            }
        }
        self.draw_format(0);
        self.draw_version(ver);
    }

    /// A 7x7 finder square and its light separator, clipped at the code's edge.
    fn draw_finder(&mut self, cx: usize, cy: usize) {
        for y in cy.saturating_sub(4)..(cy + 5).min(self.size) {
            for x in cx.saturating_sub(4)..(cx + 5).min(self.size) {
                let ring = x.abs_diff(cx).max(y.abs_diff(cy));
                self.set_fixed(x, y, ring != 2 && ring != 4);
            }
        }
    }

    fn draw_alignment(&mut self, cx: usize, cy: usize) {
        for y in cy - 2..=cy + 2 {
            for x in cx - 2..=cx + 2 {
                self.set_fixed(x, y, x.abs_diff(cx).max(y.abs_diff(cy)) != 1);
            }
        }
    }

    /// The 15 format bits (level M, then the mask) in their two copies, and the dark module.
    fn draw_format(&mut self, mask: usize) {
        let data = mask; // level M's two bits are 00
        let mut rem = data;
        for _ in 0..10 {
            rem = (rem << 1) ^ ((rem >> 9) * 0x537);
        }
        let bits = ((data << 10) | rem) ^ 0x5412;
        let bit = |i: usize| (bits >> i) & 1 != 0;
        let size = self.size;
        for i in 0..6 {
            self.set_fixed(8, i, bit(i));
        }
        self.set_fixed(8, 7, bit(6));
        self.set_fixed(8, 8, bit(7));
        self.set_fixed(7, 8, bit(8));
        for i in 9..15 {
            self.set_fixed(14 - i, 8, bit(i));
        }
        for i in 0..8 {
            self.set_fixed(size - 1 - i, 8, bit(i));
        }
        for i in 8..15 {
            self.set_fixed(8, size - 15 + i, bit(i));
        }
        self.set_fixed(8, size - 8, true);
    }

    /// From version 7 on, the version number and its error correction, in two 6x3 blocks.
    fn draw_version(&mut self, ver: usize) {
        if ver < 7 {
            return;
        }
        let mut rem = ver;
        for _ in 0..12 {
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25);
        }
        let bits = (ver << 12) | rem;
        for i in 0..18 {
            let dark = (bits >> i) & 1 != 0;
            let (a, b) = (self.size - 11 + i % 3, i / 3);
            self.set_fixed(a, b, dark);
            self.set_fixed(b, a, dark);
        }
    }

    /// Lays the codewords in the standard's zigzag: two-module-wide columns from the right,
    /// alternately up and down, skipping the vertical timing column and every fixed module.
    fn draw_codewords(&mut self, words: &[u8]) {
        let mut i = 0;
        let mut right = self.size - 1;
        loop {
            if right == 6 {
                right = 5;
            }
            let upward = (right + 1) & 2 == 0;
            for vert in 0..self.size {
                let y = if upward { self.size - 1 - vert } else { vert };
                for x in [right, right - 1] {
                    let at = y * self.size + x;
                    if !self.fixed[at] && i < words.len() * 8 {
                        self.dark[at] = (words[i / 8] >> (7 - i % 8)) & 1 != 0;
                        i += 1;
                    }
                }
            }
            if right < 2 {
                break;
            }
            right -= 2;
        }
    }

    fn apply_mask(&mut self, mask: usize) {
        for y in 0..self.size {
            for x in 0..self.size {
                let flip = match mask {
                    0 => (x + y) % 2 == 0,
                    1 => y % 2 == 0,
                    2 => x % 3 == 0,
                    3 => (x + y) % 3 == 0,
                    4 => (x / 3 + y / 2) % 2 == 0,
                    5 => x * y % 2 + x * y % 3 == 0,
                    6 => (x * y % 2 + x * y % 3) % 2 == 0,
                    _ => ((x + y) % 2 + x * y % 3) % 2 == 0,
                };
                let at = y * self.size + x;
                self.dark[at] ^= flip && !self.fixed[at];
            }
        }
    }

    /// The standard's penalty: long runs, finder-like patterns, 2x2 blocks, dark/light balance.
    fn penalty(&self) -> usize {
        let n = self.size;
        let dark = |x: usize, y: usize| self.dark[y * n + x];
        let mut score = 0;
        for i in 0..n {
            score += self.line_penalty(|j| dark(j, i));
            score += self.line_penalty(|j| dark(i, j));
        }
        for y in 0..n - 1 {
            for x in 0..n - 1 {
                let c = dark(x, y);
                if c == dark(x + 1, y) && c == dark(x, y + 1) && c == dark(x + 1, y + 1) {
                    score += 3;
                }
            }
        }
        let total = n * n;
        let darks = self.dark.iter().filter(|&&d| d).count();
        // The smallest k such that the dark share is within (45 - 5k) % to (55 + 5k) %.
        let k = (darks * 20).abs_diff(total * 10).div_ceil(total) - 1;
        score + k * 10
    }

    /// One row or column: runs of five or more same-color modules, and 1:1:3:1:1 finder-like
    /// patterns with four light modules on one side (the light border counts as light).
    fn line_penalty(&self, dark: impl Fn(usize) -> bool) -> usize {
        let n = self.size;
        let mut score = 0;
        let (mut color, mut run) = (false, 0);
        let mut history = [0; 7];
        for i in 0..n {
            if dark(i) == color {
                run += 1;
                if run == 5 {
                    score += 3;
                } else if run > 5 {
                    score += 1;
                }
            } else {
                self.push_run(run, &mut history);
                if !color {
                    score += finder_like(&history) * 40;
                }
                color = dark(i);
                run = 1;
            }
        }
        if color {
            self.push_run(run, &mut history);
            run = 0;
        }
        self.push_run(run + n, &mut history);
        score + finder_like(&history) * 40
    }

    /// Records a finished run, newest first; the first run of a line gets the light border.
    fn push_run(&self, run: usize, history: &mut [usize; 7]) {
        let border = if history[0] == 0 { self.size } else { 0 };
        history.copy_within(0..6, 1);
        history[0] = run + border;
    }
}

/// How many finder-like patterns (light, dark 1, light 1, dark 3, light 1, dark 1, light)
/// end at the newest runs, counting a side only when it has four light modules.
fn finder_like(h: &[usize; 7]) -> usize {
    let n = h[1];
    let core = n > 0 && h[2] == n && h[3] == n * 3 && h[4] == n && h[5] == n;
    usize::from(core && h[0] >= n * 4 && h[6] >= n)
        + usize::from(core && h[6] >= n * 4 && h[0] >= n)
}

fn push_bits(bits: &mut Vec<bool>, value: usize, count: usize) {
    for i in (0..count).rev() {
        bits.push((value >> i) & 1 != 0);
    }
}

/// Modules left for data and error correction once the fixed patterns are drawn.
fn raw_modules(ver: usize) -> usize {
    let mut n = (16 * ver + 128) * ver + 64;
    if ver >= 2 {
        let align = ver / 7 + 2;
        n -= (25 * align - 10) * align - 55;
        if ver >= 7 {
            n -= 36;
        }
    }
    n
}

fn data_codewords(ver: usize) -> usize {
    raw_modules(ver) / 8 - ECC_PER_BLOCK[ver] * BLOCKS[ver]
}

/// Centers of the alignment patterns along one axis.
fn alignment_positions(ver: usize) -> Vec<usize> {
    if ver == 1 {
        return Vec::new();
    }
    let count = ver / 7 + 2;
    let step = (ver * 8 + count * 3 + 5) / (count * 4 - 4) * 2;
    let mut positions: Vec<usize> = (0..count - 1).map(|i| ver * 4 + 10 - i * step).collect();
    positions.push(6);
    positions.reverse();
    positions
}

/// Splits the data into the version's blocks, adds each block's Reed-Solomon codewords and
/// interleaves them: data bytes column by column (long blocks have one more), then the
/// error correction bytes the same way.
fn interleave(ver: usize, data: &[u8]) -> Vec<u8> {
    let blocks = BLOCKS[ver];
    let ecc_len = ECC_PER_BLOCK[ver];
    let raw = raw_modules(ver) / 8;
    let short_blocks = blocks - raw % blocks;
    let short_data = raw / blocks - ecc_len;
    let divisor = rs_divisor(ecc_len);
    let mut split = Vec::with_capacity(blocks);
    let mut start = 0;
    for b in 0..blocks {
        let len = short_data + usize::from(b >= short_blocks);
        let block = &data[start..start + len];
        start += len;
        split.push((block, rs_remainder(block, &divisor)));
    }
    let mut out = Vec::with_capacity(raw);
    for i in 0..=short_data {
        out.extend(split.iter().filter_map(|(block, _)| block.get(i)));
    }
    for i in 0..ecc_len {
        out.extend(split.iter().map(|(_, ecc)| ecc[i]));
    }
    out
}

/// Multiplication in GF(2^8) modulo x^8 + x^4 + x^3 + x^2 + 1.
fn gf_mul(x: u8, y: u8) -> u8 {
    let mut z = 0u8;
    for i in (0..8).rev() {
        z = (z << 1) ^ ((z >> 7) * 0x1D);
        z ^= ((y >> i) & 1) * x;
    }
    z
}

/// The Reed-Solomon generator polynomial of the given degree, highest term first, its
/// leading 1 left out.
fn rs_divisor(degree: usize) -> Vec<u8> {
    let mut poly = vec![0u8; degree];
    poly[degree - 1] = 1;
    let mut root = 1u8;
    for _ in 0..degree {
        for j in 0..degree {
            let next = poly.get(j + 1).copied().unwrap_or(0);
            poly[j] = gf_mul(poly[j], root) ^ next;
        }
        root = gf_mul(root, 0x02);
    }
    poly
}

fn rs_remainder(data: &[u8], divisor: &[u8]) -> Vec<u8> {
    let mut rem = vec![0u8; divisor.len()];
    for &byte in data {
        let factor = byte ^ rem.remove(0);
        rem.push(0);
        for (r, &d) in rem.iter_mut().zip(divisor) {
            *r ^= gf_mul(d, factor);
        }
    }
    rem
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fingerprint(qr: &Qr) -> u64 {
        qr.dark.iter().fold(0xcbf2_9ce4_8422_2325, |h, &d| (h ^ u64::from(d)).wrapping_mul(0x100_0000_01b3))
    }

    /// Module-for-module fingerprints from Project Nayuki's qrcodegen (level M, no boost,
    /// automatic version and mask), across the version boundaries and both count-field widths.
    #[test]
    fn matches_the_reference_encoder() {
        let cases: [(usize, usize, u64); 8] = [
            (0, 21, 0x31b1_7098_8416_e8d1),    // version 1, mask 3
            (14, 21, 0xd69e_e0bd_3ed4_8ab3),   // version 1 full, mask 6
            (15, 25, 0x1c12_76cb_3bcc_8ea1),   // version 2, mask 6
            (122, 45, 0x4822_fb30_da6e_b521),  // version 7 full: version bits, mask 0
            (123, 49, 0x10bc_2068_c0c8_abad),  // version 8, mask 2
            (251, 61, 0x2469_31c1_1192_18bd),  // version 11 full: 16-bit count, mask 2
            (1000, 121, 0x4e2d_08eb_2e8e_8171), // version 26, mask 6
            (2331, 177, 0x9544_1426_e7d5_f759), // version 40 full, mask 2
        ];
        for (len, size, expected) in cases {
            let data: Vec<u8> = (0..len).map(|i| (i * 31 + 7) as u8).collect();
            let qr = encode(&data).expect("fits");
            assert_eq!(qr.size, size, "{len} bytes");
            assert_eq!(fingerprint(&qr), expected, "{len} bytes");
        }
    }

    #[test]
    fn refuses_what_no_qr_code_holds() {
        assert!(encode(&[0; 2332]).is_none());
    }

    #[test]
    fn svg_is_black_on_white_with_a_quiet_zone() {
        let qr = encode(b"https://t.me/someone_bot").expect("fits");
        let svg = qr.to_svg();
        let full = qr.size + 8;
        assert!(svg.starts_with("<svg "), "{svg}");
        assert!(svg.contains(&format!(r#"viewBox="0 0 {full} {full}""#)), "{svg}");
        assert!(svg.contains(r#"fill="white""#) && svg.contains(r#"fill="black""#), "{svg}");
        // The top-left finder's corner sits right after the four-module quiet zone.
        assert!(svg.contains(r#"d="M4,4h1v1h-1z"#), "{svg}");
        let dark = qr.dark.iter().filter(|&&d| d).count();
        assert_eq!(svg.matches("h1v1h-1z").count(), dark);
    }
}
