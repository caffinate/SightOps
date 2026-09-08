// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SightOpsApp",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "SightOps", targets: ["SightOps"])
    ],
    targets: [
        .executableTarget(
            name: "SightOps",
            path: "Sources/SightOps"
        )
    ]
)
