//! The image the guide sends, made in memory and never written anywhere (docs/proposals/guide.md
//! §4.4): a pasted PNG read with its size checked before anything is decoded, the zones the user
//! hid painted black on the capture itself, the whole reduced to 1 280 px at most by averaging, and
//! a grey band under it with this call's code, which Claude must copy back.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::io::Cursor;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

/// A pasted file larger than that is refused unread.
pub const MAX_BYTES: usize = 20 * 1024 * 1024;
/// Width and height, read in the header before decoding: a trapped image cannot fill the memory.
pub const MAX_SIDE: u32 = 8000;
/// The longer side of what Claude sees (trial 3: nothing gained above, 1.6 to 1.9 times the tokens).
pub const MAX_SENT: u32 = 1280;
/// The band under the image, which hides nothing of it and moves no coordinate.
pub const BAND: u32 = 28;
const BAND_GREY: u8 = 0x44;
/// The 5×7 font drawn 3 times larger, from 12 px in, a letter every 18 px.
const SCALE: u32 = 3;
const BAND_LEFT: u32 = 12;
const ADVANCE: u32 = 6 * SCALE;
/// The band is at least this wide, so a tiny capture still shows its whole code.
const BAND_MIN_WIDTH: u32 = BAND_LEFT * 2 + 4 * ADVANCE;
/// No letter that reads like another in 5×7 (phase 0: U taken for V; the guide v2's: V for U).
pub const ALPHABET: &[u8; 21] = b"ACDEFHJKMNPRTWXY34679";
pub const CODE_LEN: usize = 4;

/// Each letter of ALPHABET, top row first, the leftmost pixel the highest bit.
const GLYPHS: [[u8; 7]; 21] = [
    [0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001], // A
    [0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110], // C
    [0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110], // D
    [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111], // E
    [0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000], // F
    [0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001], // H
    [0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100], // J
    [0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001], // K
    [0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001], // M
    [0b10001, 0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001], // N
    [0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000], // P
    [0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001], // R
    [0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100], // T
    [0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b10101, 0b01010], // W
    [0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001], // X
    [0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100], // Y
    [0b11111, 0b00010, 0b00100, 0b00010, 0b00001, 0b10001, 0b01110], // 3
    [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010], // 4
    [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110], // 6
    [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000], // 7
    [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100], // 9
];

/// A capture as it came, 4 bytes a pixel.
pub struct Rgba {
    pub w: u32,
    pub h: u32,
    pub px: Vec<u8>,
}

/// What is sent: 3 bytes a pixel.
#[derive(Clone)]
pub struct Rgb {
    pub w: u32,
    pub h: u32,
    pub px: Vec<u8>,
}

/// A rectangle, in pixels, from the top left corner.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
pub struct Zone {
    pub x: u32,
    pub y: u32,
    pub largeur: u32,
    pub hauteur: u32,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Unreadable {
    /// More than MAX_BYTES.
    TooHeavy,
    NotPng,
    /// Wider or taller than MAX_SIDE, or empty, as its header says.
    TooLarge,
    /// A PNG that does not decode.
    Broken,
}

/// Width and height from the PNG's header (its signature, then IHDR first), nothing decoded.
fn header_size(bytes: &[u8]) -> Option<(u32, u32)> {
    const SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";
    let be = |at: usize| bytes.get(at..at + 4).map(|b| u32::from_be_bytes([b[0], b[1], b[2], b[3]]));
    (bytes.starts_with(SIGNATURE) && bytes.get(12..16) == Some(b"IHDR")).then_some(())?;
    Some((be(16)?, be(20)?))
}

/// A pasted PNG, in memory: its size checked from the header first, then decoded to 8-bit RGBA.
pub fn decode_png(bytes: &[u8]) -> Result<Rgba, Unreadable> {
    if bytes.len() > MAX_BYTES {
        return Err(Unreadable::TooHeavy);
    }
    let (w, h) = header_size(bytes).ok_or(Unreadable::NotPng)?;
    if w == 0 || h == 0 || w > MAX_SIDE || h > MAX_SIDE {
        return Err(Unreadable::TooLarge);
    }
    // The decoder's own buffers, on top of the image's: rows and the inflate window.
    let limits = png::Limits { bytes: 64 * 1024 * 1024 };
    let mut decoder = png::Decoder::new_with_limits(Cursor::new(bytes), limits);
    decoder.set_transformations(png::Transformations::normalize_to_color8());
    let mut reader = decoder.read_info().map_err(|_| Unreadable::Broken)?;
    let info = reader.info();
    if (info.width, info.height) != (w, h) {
        return Err(Unreadable::Broken);
    }
    let size = reader.output_buffer_size().ok_or(Unreadable::Broken)?;
    let mut buf = vec![0; size];
    let frame = reader.next_frame(&mut buf).map_err(|_| Unreadable::Broken)?;
    let samples: usize = match frame.color_type {
        png::ColorType::Grayscale => 1,
        png::ColorType::GrayscaleAlpha => 2,
        png::ColorType::Rgb => 3,
        png::ColorType::Rgba => 4,
        png::ColorType::Indexed => return Err(Unreadable::Broken),
    };
    if frame.bit_depth != png::BitDepth::Eight {
        return Err(Unreadable::Broken);
    }
    let mut px = Vec::with_capacity(w as usize * h as usize * 4);
    for row in buf.chunks(frame.line_size).take(h as usize) {
        for p in row.chunks_exact(samples).take(w as usize) {
            px.extend_from_slice(&match *p {
                [g] => [g, g, g, 255],
                [g, a] => [g, g, g, a],
                [r, g, b] => [r, g, b, 255],
                [r, g, b, a] => [r, g, b, a],
                _ => unreachable!("chunks_exact"),
            });
        }
    }
    if px.len() != w as usize * h as usize * 4 {
        return Err(Unreadable::Broken);
    }
    Ok(Rgba { w, h, px })
}

/// The share of black pixels (fully transparent ones count): a window minimized, hidden, or that the
/// system would not draw. A dark theme's greys stay under it.
pub fn black_share(img: &Rgba) -> f32 {
    let total = img.px.len() / 4;
    if total == 0 {
        return 1.0;
    }
    let black = img.px.chunks_exact(4).filter(|p| p[3] == 0 || (p[0] <= 6 && p[1] <= 6 && p[2] <= 6)).count();
    black as f32 / total as f32
}

/// The zones painted black, opaque, on the capture itself, before anything else is made of it.
pub fn paint(img: &mut Rgba, zones: &[Zone]) {
    for z in zones {
        let (x1, y1) = (z.x.saturating_add(z.largeur).min(img.w), z.y.saturating_add(z.hauteur).min(img.h));
        for y in z.y.min(img.h)..y1 {
            let row = (y * img.w) as usize * 4;
            for x in z.x.min(img.w)..x1 {
                img.px[row + x as usize * 4..row + x as usize * 4 + 4].copy_from_slice(&[0, 0, 0, 255]);
            }
        }
    }
}

/// A zone drawn on the image sent (`sent` its size, band left out) as the capture's pixels: every
/// capture pixel it touches, so nothing it covers on screen escapes.
pub fn to_capture(z: Zone, sent: (u32, u32), capture: (u32, u32)) -> Zone {
    let scale = |v: u32, from: u32, to: u32, up: bool| {
        let n = u64::from(v) * u64::from(to);
        let d = u64::from(from.max(1));
        (if up { n.div_ceil(d) } else { n / d }).min(u64::from(to)) as u32
    };
    let (x0, y0) = (scale(z.x, sent.0, capture.0, false), scale(z.y, sent.1, capture.1, false));
    let x1 = scale(z.x.saturating_add(z.largeur), sent.0, capture.0, true);
    let y1 = scale(z.y.saturating_add(z.hauteur), sent.1, capture.1, true);
    Zone { x: x0, y: y0, largeur: x1.saturating_sub(x0), hauteur: y1.saturating_sub(y0) }
}

/// The size of what is sent: the longer side brought to MAX_SENT at most, the proportions kept.
pub fn sent_size(w: u32, h: u32) -> (u32, u32) {
    let long = w.max(h);
    if long <= MAX_SENT {
        return (w, h);
    }
    let k = f64::from(MAX_SENT) / f64::from(long);
    let side = |v: u32| ((f64::from(v) * k).round() as u32).clamp(1, MAX_SENT);
    (side(w), side(h))
}

/// The capture over white (a window's transparent shadow becomes light), reduced by averaging every
/// source pixel by the share of it each sent pixel covers.
pub fn reduce(img: &Rgba) -> Rgb {
    let (w, h) = sent_size(img.w, img.h);
    let (sx, sy) = (f64::from(img.w) / f64::from(w), f64::from(img.h) / f64::from(h));
    let spans = |n: u32, s: f64, of: u32| -> Vec<Vec<(usize, f32)>> {
        (0..n)
            .map(|i| {
                let (a, b) = (f64::from(i) * s, (f64::from(i) + 1.0) * s);
                (a.floor() as u32..(b.ceil() as u32).min(of))
                    .map(|j| (j as usize, ((b.min(f64::from(j) + 1.0) - a.max(f64::from(j))) / s) as f32))
                    .filter(|(_, wt)| *wt > 0.0)
                    .collect()
            })
            .collect()
    };
    let (cols, rows) = (spans(w, sx, img.w), spans(h, sy, img.h));
    let mut out = Vec::with_capacity(w as usize * h as usize * 3);
    let mut line = vec![0f32; img.w as usize * 3];
    for row in &rows {
        line.iter_mut().for_each(|v| *v = 0.0);
        for &(y, wy) in row {
            let src = &img.px[y * img.w as usize * 4..(y + 1) * img.w as usize * 4];
            for (x, p) in src.chunks_exact(4).enumerate() {
                let a = f32::from(p[3]) / 255.0;
                for c in 0..3 {
                    line[x * 3 + c] += wy * (f32::from(p[c]) * a + 255.0 * (1.0 - a));
                }
            }
        }
        for col in &cols {
            for c in 0..3 {
                let v: f32 = col.iter().map(|&(x, wx)| wx * line[x * 3 + c]).sum();
                out.push(v.round().clamp(0.0, 255.0) as u8);
            }
        }
    }
    Rgb { w, h, px: out }
}

/// The image with the band under it and `code` in it, white on grey. Coordinates do not move.
pub fn with_band(img: &Rgb, code: &str) -> Rgb {
    let w = img.w.max(BAND_MIN_WIDTH);
    let h = img.h + BAND;
    let mut px = vec![BAND_GREY; w as usize * h as usize * 3];
    for y in 0..img.h as usize {
        let from = y * img.w as usize * 3;
        px[y * w as usize * 3..y * w as usize * 3 + img.w as usize * 3].copy_from_slice(&img.px[from..from + img.w as usize * 3]);
    }
    let top = img.h + (BAND - 7 * SCALE) / 2;
    for (i, letter) in code.bytes().enumerate() {
        let Some(glyph) = ALPHABET.iter().position(|&c| c == letter).map(|g| GLYPHS[g]) else { continue };
        for (r, bits) in glyph.iter().enumerate() {
            for col in 0..5u32 {
                if bits & (0b10000 >> col) == 0 {
                    continue;
                }
                for dy in 0..SCALE {
                    for dx in 0..SCALE {
                        let x = BAND_LEFT + i as u32 * ADVANCE + col * SCALE + dx;
                        let y = top + r as u32 * SCALE + dy;
                        if x < w {
                            let at = (y as usize * w as usize + x as usize) * 3;
                            px[at..at + 3].copy_from_slice(&[255, 255, 255]);
                        }
                    }
                }
            }
        }
    }
    Rgb { w, h, px }
}

pub fn encode_png(img: &Rgb) -> Vec<u8> {
    let mut out = Vec::new();
    let mut encoder = png::Encoder::new(&mut out, img.w, img.h);
    encoder.set_color(png::ColorType::Rgb);
    encoder.set_depth(png::BitDepth::Eight);
    // Writing to memory with sizes the image itself gives cannot fail.
    if let Ok(mut writer) = encoder.write_header() {
        let _ = writer.write_image_data(&img.px);
    }
    out
}

static DRAWN: AtomicU64 = AtomicU64::new(0);

/// A new code, for each call: the system's randomness (std's hash keys) over the time and a count.
pub fn new_code() -> String {
    let mut h = RandomState::new().build_hasher();
    h.write_u128(SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_nanos()));
    h.write_u64(DRAWN.fetch_add(1, Ordering::Relaxed));
    let mut n = h.finish();
    (0..CODE_LEN)
        .map(|_| {
            let c = ALPHABET[(n % ALPHABET.len() as u64) as usize];
            n /= ALPHABET.len() as u64;
            char::from(c)
        })
        .collect()
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub fn rgba(w: u32, h: u32, color: [u8; 4]) -> Rgba {
        Rgba { w, h, px: color.repeat(w as usize * h as usize) }
    }

    pub fn png_of(w: u32, h: u32, color: png::ColorType, depth: png::BitDepth, data: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        let mut e = png::Encoder::new(&mut out, w, h);
        e.set_color(color);
        e.set_depth(depth);
        e.write_header().unwrap().write_image_data(data).unwrap();
        out
    }

    fn pixel(img: &Rgb, x: u32, y: u32) -> [u8; 3] {
        let at = (y * img.w + x) as usize * 3;
        [img.px[at], img.px[at + 1], img.px[at + 2]]
    }

    /// Reads the band back, as Claude should: each cell against the font, nothing else drawn there.
    pub fn read_band(img: &Rgb, h: u32) -> Option<String> {
        let top = h + (BAND - 7 * SCALE) / 2;
        let mut code = String::new();
        for i in 0..CODE_LEN as u32 {
            let mut cell = [0u8; 7];
            for (r, bits) in cell.iter_mut().enumerate() {
                for col in 0..5 {
                    let (x, y) = (BAND_LEFT + i * ADVANCE + col * SCALE + 1, top + r as u32 * SCALE + 1);
                    match pixel(img, x, y) {
                        [255, 255, 255] => *bits |= 0b10000 >> col,
                        [BAND_GREY, BAND_GREY, BAND_GREY] => {}
                        _ => return None,
                    }
                }
            }
            code.push(char::from(ALPHABET[GLYPHS.iter().position(|g| *g == cell)?]));
        }
        Some(code)
    }

    #[test]
    fn the_font_has_one_shape_per_letter() {
        for (i, g) in GLYPHS.iter().enumerate() {
            assert!(g.iter().all(|row| *row < 32), "{}", ALPHABET[i] as char);
            assert_eq!(GLYPHS.iter().filter(|o| *o == g).count(), 1, "{}", ALPHABET[i] as char);
        }
        assert!(!ALPHABET.contains(&b'U'), "U reads as V (phase 0)");
        assert!(!ALPHABET.contains(&b'V'), "V reads as U (guide v2's P0: 2 codes of 12)");
        assert!(!ALPHABET.iter().any(|c| b"BGILOQSZ01258".contains(c)));
    }

    #[test]
    fn sizes_after_reduction() {
        assert_eq!(sent_size(1600, 1000), (1280, 800), "landscape");
        assert_eq!(sent_size(1000, 1600), (800, 1280), "portrait");
        assert_eq!(sent_size(2880, 1800), (1280, 800), "Retina");
        assert_eq!(sent_size(1280, 720), (1280, 720), "already small enough");
        assert_eq!(sent_size(150, 90), (150, 90), "a tiny window");
        assert_eq!(sent_size(8000, 3), (1280, 1), "never zero");
        let r = reduce(&rgba(2880, 1800, [10, 20, 30, 255]));
        assert_eq!((r.w, r.h, r.px.len()), (1280, 800, 1280 * 800 * 3));
        assert!(r.px.chunks(3).all(|p| p == [10, 20, 30]), "a flat colour stays itself");
    }

    #[test]
    fn reduction_averages_what_each_pixel_covers() {
        // Two columns black, two white, out of 2560: each sent pixel covers exactly two source ones.
        let mut img = rgba(2560, 2, [255, 255, 255, 255]);
        for y in 0..2 {
            for x in (0..2560).step_by(4) {
                for k in 0..2 {
                    let at = ((y * 2560 + x + k) * 4) as usize;
                    img.px[at..at + 3].copy_from_slice(&[0, 0, 0]);
                }
            }
        }
        let r = reduce(&img);
        assert_eq!((r.w, r.h), (1280, 1));
        assert_eq!(pixel(&r, 0, 0), [0, 0, 0]);
        assert_eq!(pixel(&r, 1, 0), [255, 255, 255]);
        // Transparent over white.
        let r = reduce(&rgba(1300, 10, [0, 0, 0, 0]));
        assert!(r.px.iter().all(|&v| v == 255));
    }

    #[test]
    fn masks_go_on_before_the_reduction() {
        let mut img = rgba(2560, 1600, [200, 200, 200, 255]);
        // One capture pixel only: after halving, its sent pixel is a quarter black, not untouched.
        paint(&mut img, &[Zone { x: 10, y: 10, largeur: 1, hauteur: 1 }]);
        let r = reduce(&img);
        assert_eq!(pixel(&r, 5, 5), [150, 150, 150]);
        assert_eq!(pixel(&r, 6, 5), [200, 200, 200]);
        // A zone past the edges is cut at them.
        paint(&mut img, &[Zone { x: 2550, y: 1590, largeur: 500, hauteur: 500 }]);
        assert_eq!(&img.px[img.px.len() - 4..], &[0, 0, 0, 255]);
    }

    #[test]
    fn a_zone_on_the_sent_image_covers_every_capture_pixel_it_touches() {
        let z = to_capture(Zone { x: 1, y: 1, largeur: 1, hauteur: 1 }, (1280, 800), (2880, 1800));
        assert_eq!(z, Zone { x: 2, y: 2, largeur: 3, hauteur: 3 }, "2.25 to 4.5, both ways");
        let whole = to_capture(Zone { x: 0, y: 0, largeur: 5000, hauteur: 5000 }, (1280, 800), (2880, 1800));
        assert_eq!(whole, Zone { x: 0, y: 0, largeur: 2880, hauteur: 1800 });
        assert_eq!(to_capture(Zone { x: 3, y: 4, largeur: 5, hauteur: 6 }, (800, 600), (800, 600)), Zone { x: 3, y: 4, largeur: 5, hauteur: 6 });
    }

    #[test]
    fn the_band_holds_the_code_under_the_image() {
        let img = reduce(&rgba(1600, 1000, [0, 128, 0, 255]));
        for code in ["NRYW", "AC34", "9999", "WXYJ"] {
            let sent = with_band(&img, code);
            assert_eq!((sent.w, sent.h), (1280, 800 + BAND));
            assert_eq!(read_band(&sent, 800).as_deref(), Some(code));
            assert_eq!(pixel(&sent, 1279, 799), [0, 128, 0], "the image is untouched above the band");
            assert_eq!(pixel(&sent, 1279, 800), [BAND_GREY; 3]);
            // Phase 0's layout, to the pixel: 3 grey rows, the 21 of the letters, 4 grey rows; 12 px in.
            let lit = |y: u32| (0..sent.w).any(|x| pixel(&sent, x, y) == [255, 255, 255]);
            let rows: Vec<bool> = (800..800 + BAND).map(lit).collect();
            assert_eq!(rows.iter().position(|l| *l), Some(3), "{code}");
            assert_eq!(rows.iter().rposition(|l| *l), Some(23), "{code}");
            let first = (0..sent.w).find(|&x| (800..800 + BAND).any(|y| pixel(&sent, x, y) == [255, 255, 255]));
            assert_eq!(first, Some(BAND_LEFT + if code.starts_with('J') { 2 * SCALE } else { 0 }), "{code}");
        }
        // A capture narrower than the code: the band is wider, the image stays where it was.
        let tiny = with_band(&reduce(&rgba(40, 30, [9, 9, 9, 255])), "MNPR");
        assert_eq!((tiny.w, tiny.h), (BAND_MIN_WIDTH, 30 + BAND));
        assert_eq!(read_band(&tiny, 30).as_deref(), Some("MNPR"));
        assert_eq!((pixel(&tiny, 39, 29), pixel(&tiny, 40, 29)), ([9, 9, 9], [BAND_GREY; 3]));
    }

    #[test]
    fn codes_change_and_use_the_alphabet() {
        let codes: std::collections::HashSet<String> = (0..200).map(|_| new_code()).collect();
        assert!(codes.len() > 190, "{}", codes.len());
        assert!(codes.iter().all(|c| c.len() == CODE_LEN && c.bytes().all(|b| ALPHABET.contains(&b))));
    }

    #[test]
    fn png_round_trip() {
        let img = with_band(&reduce(&rgba(300, 200, [1, 2, 3, 255])), "TWXY");
        let back = decode_png(&encode_png(&img)).unwrap();
        assert_eq!((back.w, back.h), (img.w, img.h));
        let rgb: Vec<u8> = back.px.chunks(4).flat_map(|p| p[..3].to_vec()).collect();
        assert!(rgb == img.px);
    }

    #[test]
    fn every_kind_of_png_is_read_as_rgba() {
        use png::{BitDepth::*, ColorType::*};
        let gray = decode_png(&png_of(2, 1, Grayscale, Eight, &[0, 255])).unwrap();
        assert_eq!(gray.px, [0, 0, 0, 255, 255, 255, 255, 255]);
        let ga = decode_png(&png_of(1, 1, GrayscaleAlpha, Eight, &[7, 0])).unwrap();
        assert_eq!(ga.px, [7, 7, 7, 0]);
        let deep = decode_png(&png_of(1, 1, Rgb, Sixteen, &[0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc])).unwrap();
        assert_eq!(deep.px, [0x12, 0x56, 0x9a, 255]);
        let bits = decode_png(&png_of(8, 1, Grayscale, One, &[0b1000_0001])).unwrap();
        assert_eq!(&bits.px[..4], &[255, 255, 255, 255]);
        assert_eq!(&bits.px[4..8], &[0, 0, 0, 255]);
    }

    #[test]
    fn a_trapped_or_wrong_file_is_refused_before_decoding() {
        // A header announcing 100 000 × 100 000, no data behind it.
        let mut huge = png_of(1, 1, png::ColorType::Rgb, png::BitDepth::Eight, &[0, 0, 0]);
        huge[16..20].copy_from_slice(&100_000u32.to_be_bytes());
        huge[20..24].copy_from_slice(&100_000u32.to_be_bytes());
        assert_eq!(decode_png(&huge).err(), Some(Unreadable::TooLarge));
        let mut wide = huge.clone();
        wide[16..20].copy_from_slice(&8001u32.to_be_bytes());
        wide[20..24].copy_from_slice(&10u32.to_be_bytes());
        assert_eq!(decode_png(&wide).err(), Some(Unreadable::TooLarge));
        assert_eq!(decode_png(b"GIF89a....").err(), Some(Unreadable::NotPng));
        assert_eq!(decode_png(b"\xff\xd8\xff\xe0 a jpeg").err(), Some(Unreadable::NotPng));
        assert_eq!(decode_png(&vec![0; MAX_BYTES + 1]).err(), Some(Unreadable::TooHeavy));
        let good = png_of(4, 4, png::ColorType::Rgb, png::BitDepth::Eight, &[9; 48]);
        assert_eq!(decode_png(&good[..good.len() - 20]).err(), Some(Unreadable::Broken), "cut short");
        // The header says one size, the decoder would read another: refused (CRC of IHDR fixed).
        let mut liar = png_of(4, 4, png::ColorType::Rgb, png::BitDepth::Eight, &[9; 48]);
        liar[19] = 5;
        assert!(decode_png(&liar).is_err());
    }

    #[test]
    fn the_black_share_counts_black_and_transparent_but_not_a_dark_theme() {
        let mut img = rgba(10, 10, [30, 30, 30, 255]);
        assert_eq!(black_share(&img), 0.0);
        paint(&mut img, &[Zone { x: 0, y: 0, largeur: 10, hauteur: 7 }]);
        assert!((black_share(&img) - 0.7).abs() < 1e-6);
        assert_eq!(black_share(&rgba(4, 4, [255, 255, 255, 0])), 1.0);
    }
}
