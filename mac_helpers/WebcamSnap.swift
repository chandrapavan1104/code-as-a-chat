// WebcamSnap — minimal one-shot webcam capture for Code-as-a-Chat.
// Usage: WebcamSnap <output.jpg>
//
// Uses AVCaptureVideoDataOutput (raw frame grab) rather than AVCapturePhotoOutput,
// because the latter's Obj-C KVO machinery doesn't initialize in a bare
// swiftc-built CLI binary. Grabs a frame after a short warm-up, encodes JPEG.
//
// Lives inside WebcamSnap.app so macOS TCC grants it a stable Camera identity.
// Build:
//   swiftc -O -o WebcamSnap.app/Contents/MacOS/WebcamSnap WebcamSnap.swift \
//          -framework AVFoundation -framework CoreImage -framework Foundation

import AVFoundation
import CoreImage
import Foundation

let outPath = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "/tmp/webcamsnap.jpg"

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write(("ERROR: " + msg + "\n").data(using: .utf8)!)
    exit(1)
}

// Explicit authorization check so failures are self-explanatory.
switch AVCaptureDevice.authorizationStatus(for: .video) {
case .authorized:
    break
case .notDetermined:
    let authSem = DispatchSemaphore(value: 0)
    var allowed = false
    AVCaptureDevice.requestAccess(for: .video) { granted in
        allowed = granted
        authSem.signal()
    }
    authSem.wait()
    if !allowed { fail("camera permission denied at prompt") }
case .denied:
    fail("camera permission DENIED — enable WebcamSnap in "
         + "System Settings > Privacy & Security > Camera")
case .restricted:
    fail("camera access restricted by system policy")
@unknown default:
    fail("unknown camera authorization status")
}

let session = AVCaptureSession()
session.sessionPreset = .photo

guard let device = AVCaptureDevice.default(for: .video) else {
    fail("no camera device found")
}

do {
    let input = try AVCaptureDeviceInput(device: device)
    guard session.canAddInput(input) else { fail("cannot add camera input") }
    session.addInput(input)
} catch {
    fail("camera input error: \(error.localizedDescription)")
}

let output = AVCaptureVideoDataOutput()
output.videoSettings = [
    kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA)
]
output.alwaysDiscardsLateVideoFrames = true

final class FrameGrabber: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let path: String
    let warmupFrames = 6          // let auto-exposure / white-balance settle
    var seen = 0
    let ctx = CIContext()

    init(path: String) { self.path = path }

    func captureOutput(_ output: AVCaptureOutput,
                       didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        seen += 1
        if seen < warmupFrames { return }
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let ciImage = CIImage(cvImageBuffer: pixelBuffer)
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let data = ctx.jpegRepresentation(of: ciImage,
                                                colorSpace: colorSpace,
                                                options: [:]) else {
            fail("jpeg encoding failed")
        }
        do {
            try data.write(to: URL(fileURLWithPath: path))
            exit(0)
        } catch {
            fail("write file: \(error.localizedDescription)")
        }
    }
}

let grabber = FrameGrabber(path: outPath)
let queue = DispatchQueue(label: "com.codeasachat.webcamsnap.frames")
output.setSampleBufferDelegate(grabber, queue: queue)
guard session.canAddOutput(output) else { fail("cannot add video output") }
session.addOutput(output)

session.startRunning()

// Fail-safe timeout; the delegate exits(0) on the first good frame.
DispatchQueue.global().asyncAfter(deadline: .now() + 12) {
    fail("capture timed out")
}

RunLoop.main.run()
