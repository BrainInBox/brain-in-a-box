//! macOS 14 and later: the window the user picks in the system's own picker (SCContentSharingPicker:
//! one window, Bibo's left out), then one image of it, from a stream opened for it and stopped at once
//! (SCStream). No « Enregistrement de l'écran » permission: the picker is the user's consent, and the
//! system shows its sharing indicator while the guide keeps the window. Phase 0 (spec §15):
//! SCScreenshotManager and SCShareableContent want that permission and bring up macOS's alert, so
//! neither is ever called here. macOS 13 has no picker: paste mode.

use std::cell::RefCell;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use block2::RcBlock;
use dispatch2::DispatchQueue;
use objc2::rc::Retained;
use objc2::runtime::{AnyObject, ProtocolObject};
use objc2::{define_class, msg_send, AnyThread, DefinedClass, MainThreadMarker, Message};
use objc2_core_foundation::CFArray;
use objc2_core_media::CMSampleBuffer;
use objc2_core_video::{
    kCVPixelFormatType_32BGRA, CVPixelBuffer, CVPixelBufferGetBaseAddress, CVPixelBufferGetBytesPerRow, CVPixelBufferGetHeight,
    CVPixelBufferGetPixelFormatType, CVPixelBufferGetWidth, CVPixelBufferLockBaseAddress, CVPixelBufferLockFlags, CVPixelBufferUnlockBaseAddress,
};
use objc2_foundation::{NSArray, NSBundle, NSDictionary, NSError, NSNumber, NSObject, NSObjectProtocol, NSString};
use objc2_screen_capture_kit::{
    SCContentFilter, SCContentSharingPicker, SCContentSharingPickerConfiguration, SCContentSharingPickerMode, SCContentSharingPickerObserver, SCFrameStatus,
    SCShareableContentStyle, SCStream, SCStreamConfiguration, SCStreamDelegate, SCStreamFrameInfoStatus, SCStreamOutput, SCStreamOutputType,
};

use super::{from_bgra_rows, is_terminal, Found, Pick, Rate, Window};
use crate::shot::{Rgba, MAX_SIDE};

/// Bibo's bundle id, left out of the picker, when the program running has none of its own.
const BIBO: &str = "com.braininbox.installer";
/// How long the picker waits for the user's choice.
const CHOIX: Duration = Duration::from_secs(300);
/// How long a stream has to give its first whole image.
const IMAGE: Duration = Duration::from_secs(5);
const POLL: Duration = Duration::from_millis(100);
/// ScreenCaptureKit's codes (SCStreamErrorCode): the user declined (the permission), no capture source
/// left (the window closed).
const DECLINED: isize = -3801;
const NO_SOURCE: isize = -3815;

pub fn available() -> bool {
    objc2::available!(macos = 14.0)
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

/// What another thread sets once, waited for here.
struct Slot<T> {
    value: Mutex<Option<T>>,
    ready: Condvar,
}

impl<T> Slot<T> {
    fn new() -> Arc<Self> {
        Arc::new(Slot { value: Mutex::new(None), ready: Condvar::new() })
    }

    /// The first value stays: a second answer of the system's changes nothing.
    fn set(&self, v: T) {
        let mut value = lock(&self.value);
        if value.is_none() {
            *value = Some(v);
            self.ready.notify_all();
        }
    }

    /// The value once set; None after `limit`, or as soon as `stop` says so.
    fn wait(&self, limit: Duration, stop: &dyn Fn() -> bool) -> Option<T> {
        let start = Instant::now();
        let mut value = lock(&self.value);
        loop {
            if let Some(v) = value.take() {
                return Some(v);
            }
            if stop() || start.elapsed() >= limit {
                return None;
            }
            value = self.ready.wait_timeout(value, POLL).unwrap_or_else(PoisonError::into_inner).0;
        }
    }
}

/// The system's filter: the window the user picked, and nothing else.
#[derive(Clone)]
struct Filtre(Retained<SCContentFilter>);

// SAFETY: a value ScreenCaptureKit makes once and never changes; Bibo only reads its frame and hands it
// to a stream, from one thread at a time (CHOSEN's lock).
unsafe impl Send for Filtre {}

enum Picked {
    Filtre(Filtre),
    Fermee,
    Refus(String),
}

define_class!(
    // SAFETY: NSObject has no subclassing requirements, and this class has no Drop.
    #[unsafe(super(NSObject))]
    #[ivars = Arc<Slot<Picked>>]
    struct Observer;

    unsafe impl NSObjectProtocol for Observer {}

    unsafe impl SCContentSharingPickerObserver for Observer {
        #[unsafe(method(contentSharingPicker:didCancelForStream:))]
        fn did_cancel(&self, _picker: &SCContentSharingPicker, _stream: Option<&SCStream>) {
            self.ivars().set(Picked::Fermee);
        }

        #[unsafe(method(contentSharingPicker:didUpdateWithFilter:forStream:))]
        fn did_update(&self, _picker: &SCContentSharingPicker, filter: &SCContentFilter, _stream: Option<&SCStream>) {
            self.ivars().set(Picked::Filtre(Filtre(filter.retain())));
        }

        #[unsafe(method(contentSharingPickerStartDidFailWithError:))]
        fn did_fail(&self, error: &NSError) {
            self.ivars().set(Picked::Refus(error.localizedDescription().to_string()));
        }
    }
);

impl Observer {
    fn new(slot: Arc<Slot<Picked>>) -> Retained<Self> {
        let this = Self::alloc().set_ivars(slot);
        // SAFETY: NSObject's own initializer.
        unsafe { msg_send![super(this), init] }
    }
}

thread_local! {
    /// The observer while the picker is out (the main thread's: the picker shows and goes there).
    static WATCHING: RefCell<Option<Retained<Observer>>> = const { RefCell::new(None) };
}

/// The window the picker gave, for the session.
struct Chosen {
    found: Found,
    filter: Filtre,
}

static CHOSEN: Mutex<Option<Chosen>> = Mutex::new(None);
/// The picker was shown once in this run: it has something to put away.
static SHOWN: AtomicBool = AtomicBool::new(false);
static NEXT: AtomicU64 = AtomicU64::new(0);

/// On the main thread: the picker, one window, Bibo's own windows left out.
fn show(slot: Arc<Slot<Picked>>) {
    let own = NSBundle::mainBundle().bundleIdentifier().unwrap_or_else(|| NSString::from_str(BIBO));
    // SAFETY: the system's shared picker and a configuration of ours, on the main thread.
    unsafe {
        let picker = SCContentSharingPicker::sharedPicker();
        let conf: Retained<SCContentSharingPickerConfiguration> = SCContentSharingPickerConfiguration::new();
        conf.setAllowedPickerModes(SCContentSharingPickerMode::SingleWindow);
        conf.setExcludedBundleIDs(&NSArray::from_retained_slice(&[own]));
        conf.setAllowsChangingSelectedContent(false);
        picker.setDefaultConfiguration(&conf);
        let observer = Observer::new(slot);
        picker.addObserver(ProtocolObject::from_ref(&*observer));
        WATCHING.with(|w| *w.borrow_mut() = Some(observer));
        picker.setActive(true);
        picker.presentPickerUsingContentStyle(SCShareableContentStyle::Window);
    }
}

/// On the main thread: the observer gone; with `off`, the picker too (its window, its indicator).
fn put_away(off: bool) {
    // SAFETY: the system's shared picker, on the main thread; the observer added by `show`.
    unsafe {
        let picker = SCContentSharingPicker::sharedPicker();
        if let Some(o) = WATCHING.with(|w| w.borrow_mut().take()) {
            picker.removeObserver(ProtocolObject::from_ref(&*o));
        }
        if off {
            picker.setActive(false);
        }
    }
}

/// What the system says of the window picked: its title, program and process since macOS 15.2,
/// as they were at the choice; nothing before.
fn describe_filter(filter: &SCContentFilter) -> Found {
    let id = NEXT.fetch_add(1, Ordering::SeqCst) + 1;
    let mut found = Found { window: Window { id, pid: 0, app: String::new() }, nom: String::new(), titre: String::new(), reduite: false, terminal: false };
    if objc2::available!(macos = 15.2) {
        // SAFETY: properties of the filter and of its window, there since macOS 15.2.
        unsafe {
            if let Some(w) = filter.includedWindows().firstObject() {
                found.titre = w.title().map(|t| t.to_string()).unwrap_or_default();
                if let Some(app) = w.owningApplication() {
                    found.window.pid = u32::try_from(app.processID()).unwrap_or(0);
                    found.window.app = app.bundleIdentifier().to_string();
                    found.nom = app.applicationName().to_string();
                }
            }
        }
    }
    found.terminal = is_terminal(&found.window.app, "");
    found
}

/// The system's picker, until the user picks a window or closes it, or Bibo stops waiting. Never on
/// the main thread, which shows it: a command's own thread waits here.
pub fn pick(cancel: &AtomicBool) -> Pick {
    if !available() || MainThreadMarker::new().is_some() {
        return Pick::Arrete;
    }
    SHOWN.store(true, Ordering::SeqCst);
    let slot = Slot::new();
    let shown = slot.clone();
    DispatchQueue::main().exec_async(move || show(shown));
    let picked = slot.wait(CHOIX, &|| cancel.load(Ordering::SeqCst));
    // A window still kept from before keeps the picker on (its stream needs it); a new one too.
    let off = match &picked {
        Some(Picked::Filtre(_)) => false,
        Some(_) => lock(&CHOSEN).is_none(),
        // Bibo stopped waiting: the picker must not stay out on the screen.
        None => true,
    };
    DispatchQueue::main().exec_async(move || put_away(off));
    match picked {
        Some(Picked::Filtre(filter)) => {
            let found = describe_filter(&filter.0);
            *lock(&CHOSEN) = Some(Chosen { found: found.clone(), filter });
            Pick::Choisie(found)
        }
        Some(Picked::Fermee) => Pick::Fermee,
        Some(Picked::Refus(raison)) => Pick::Refus(raison),
        None => Pick::Arrete,
    }
}

/// The guide closed: the window forgotten, the picker off (and the system's indicator with it).
pub fn forget() {
    *lock(&CHOSEN) = None;
    if available() && SHOWN.load(Ordering::SeqCst) {
        DispatchQueue::main().exec_async(|| put_away(true));
    }
}

/// No list of windows without the screen recording permission: the picker is the only way.
pub fn list() -> Vec<Found> {
    Vec::new()
}

pub fn foreground() -> Option<Found> {
    None
}

/// The window the picker gave, if this is its id.
pub fn describe(id: u64) -> Option<Found> {
    lock(&CHOSEN).as_ref().filter(|c| c.found.window.id == id).map(|c| c.found.clone())
}

/// The window picked, one image of it now.
pub fn grab(chosen: &Window) -> Result<Rgba, Rate> {
    if !available() {
        return Err(Rate::Indisponible);
    }
    let filter = lock(&CHOSEN).as_ref().filter(|c| c.found.window == *chosen).map(|c| c.filter.clone()).ok_or(Rate::Introuvable)?;
    one_image(&filter.0)
}

fn rate_of(error: &NSError) -> Rate {
    match error.code() {
        DECLINED => Rate::Indisponible,
        NO_SOURCE => Rate::Introuvable,
        _ => Rate::Noire,
    }
}

define_class!(
    // SAFETY: NSObject has no subclassing requirements, and this class has no Drop.
    #[unsafe(super(NSObject))]
    #[ivars = Arc<Slot<Result<Rgba, Rate>>>]
    struct Output;

    unsafe impl NSObjectProtocol for Output {}

    unsafe impl SCStreamOutput for Output {
        #[unsafe(method(stream:didOutputSampleBuffer:ofType:))]
        fn did_output(&self, _stream: &SCStream, sample: &CMSampleBuffer, kind: SCStreamOutputType) {
            if kind == SCStreamOutputType::Screen {
                if let Some(image) = whole_image(sample) {
                    self.ivars().set(image);
                }
            }
        }
    }

    unsafe impl SCStreamDelegate for Output {
        #[unsafe(method(stream:didStopWithError:))]
        fn did_stop(&self, _stream: &SCStream, error: &NSError) {
            self.ivars().set(Err(rate_of(error)));
        }
    }
);

impl Output {
    fn new(slot: Arc<Slot<Result<Rgba, Rate>>>) -> Retained<Self> {
        let this = Self::alloc().set_ivars(slot);
        // SAFETY: NSObject's own initializer.
        unsafe { msg_send![super(this), init] }
    }
}

/// A frame the stream says complete, read; None for the others (idle, blank, started...).
fn whole_image(sample: &CMSampleBuffer) -> Option<Result<Rgba, Rate>> {
    // SAFETY: the stream's buffer, read during its callback.
    let infos = unsafe { sample.sample_attachments_array(false) }?;
    // SAFETY: CFArray and NSArray are toll-free bridged, as are its CFDictionaries and NSDictionary.
    let infos: &NSArray<NSDictionary<NSString, AnyObject>> = unsafe { &*(&*infos as *const CFArray).cast() };
    // SAFETY: a key ScreenCaptureKit defines.
    let status = infos.firstObject()?.objectForKey(unsafe { SCStreamFrameInfoStatus })?;
    if status.downcast::<NSNumber>().ok()?.integerValue() != SCFrameStatus::Complete.0 {
        return None;
    }
    // SAFETY: as above.
    let pixels = unsafe { sample.image_buffer() }?;
    Some(read(&pixels).ok_or(Rate::Noire))
}

/// The pixels of a CoreVideo buffer in BGRA, copied while it is locked for reading.
fn read(pixels: &CVPixelBuffer) -> Option<Rgba> {
    if CVPixelBufferGetPixelFormatType(pixels) != kCVPixelFormatType_32BGRA {
        return None;
    }
    // SAFETY: locked for reading, unlocked below; its memory read only in between.
    if unsafe { CVPixelBufferLockBaseAddress(pixels, CVPixelBufferLockFlags::ReadOnly) } != 0 {
        return None;
    }
    let (w, h, stride, base) =
        (CVPixelBufferGetWidth(pixels), CVPixelBufferGetHeight(pixels), CVPixelBufferGetBytesPerRow(pixels), CVPixelBufferGetBaseAddress(pixels));
    let image = (!base.is_null() && w <= MAX_SIDE as usize && h <= MAX_SIDE as usize)
        // SAFETY: `h` rows of `stride` bytes from the base address, while the buffer is locked.
        .then(|| from_bgra_rows(unsafe { std::slice::from_raw_parts(base.cast::<u8>(), stride * h) }, w as u32, h as u32, stride))
        .flatten();
    // SAFETY: locked above.
    unsafe { CVPixelBufferUnlockBaseAddress(pixels, CVPixelBufferLockFlags::ReadOnly) };
    image
}

/// One image of the window: a stream opened for it, its first complete frame, then stopped.
fn one_image(filter: &SCContentFilter) -> Result<Rgba, Rate> {
    // SAFETY: properties of the filter.
    let (rect, scale) = unsafe { (filter.contentRect(), f64::from(filter.pointPixelScale())) };
    let (w, h) = ((rect.size.width * scale).round(), (rect.size.height * scale).round());
    if !(w >= 1.0 && h >= 1.0 && w <= f64::from(MAX_SIDE) && h <= f64::from(MAX_SIDE)) {
        return Err(Rate::Noire);
    }
    let slot: Arc<Slot<Result<Rgba, Rate>>> = Slot::new();
    let output = Output::new(slot.clone());
    let queue = DispatchQueue::new("com.braininbox.guide.image", None);
    // SAFETY: a configuration and a stream of ours, our output and its queue kept until the end.
    let stream = unsafe {
        let conf = SCStreamConfiguration::new();
        conf.setWidth(w as usize);
        conf.setHeight(h as usize);
        conf.setPixelFormat(kCVPixelFormatType_32BGRA);
        conf.setShowsCursor(false);
        conf.setIgnoreShadowsSingleWindow(true);
        conf.setQueueDepth(3);
        let stream = SCStream::initWithFilter_configuration_delegate(SCStream::alloc(), filter, &conf, Some(ProtocolObject::from_ref(&*output)));
        if stream.addStreamOutput_type_sampleHandlerQueue_error(ProtocolObject::from_ref(&*output), SCStreamOutputType::Screen, Some(&queue)).is_err() {
            return Err(Rate::Noire);
        }
        stream
    };
    let started = slot.clone();
    let on_start = RcBlock::new(move |error: *mut NSError| {
        // SAFETY: ScreenCaptureKit's error, or null.
        if let Some(e) = unsafe { error.as_ref() } {
            started.set(Err(rate_of(e)));
        }
    });
    // SAFETY: our stream; the block is copied by the call.
    unsafe { stream.startCaptureWithCompletionHandler(Some(&on_start)) };
    let image = slot.wait(IMAGE, &|| false).unwrap_or(Err(Rate::Noire));
    // SAFETY: as above; nothing waits for the stop.
    unsafe { stream.stopCaptureWithCompletionHandler(None) };
    image
}
