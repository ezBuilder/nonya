// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "NonyaPet",
    platforms: [.macOS(.v14)],     // ScreenCaptureKit SCScreenshotManager.captureImage (M3 OCR) needs 14+
    targets: [
        .executableTarget(
            name: "NonyaPet",
            path: "Sources/NonyaPet",
            resources: [.copy("Resources")]
        )
    ]
)
