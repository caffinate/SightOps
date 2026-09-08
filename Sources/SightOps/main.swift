import SwiftUI
import SwiftData

@main
struct SightOpsApp: App {
    private let container: ModelContainer

    init() {
        do {
            let schema = Schema([LocalTask.self, SyncState.self, ActivityEvent.self])
            let configuration = ModelConfiguration("SightOps", schema: schema)
            container = try ModelContainer(for: schema, configurations: configuration)
        } catch {
            fatalError("Unable to create SightOps local store: \(error)")
        }
    }

    var body: some Scene {
        WindowGroup("SightOps") {
            ContentView()
        }
        .modelContainer(container)
        .defaultSize(width: 1100, height: 720)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Refresh from Hermes") {
                    NotificationCenter.default.post(name: .sightOpsRefreshRequested, object: nil)
                }
                .keyboardShortcut("r", modifiers: [.command])
            }
        }
    }
}

extension Notification.Name {
    static let sightOpsRefreshRequested = Notification.Name("SightOpsRefreshRequested")
}

@Model
final class LocalTask {
    @Attribute(.unique) var pageID: String
    var title: String
    var status: String
    var source: String
    var priority: String
    var dueDate: String
    var pageURL: String
    var updatedAt: Date

    init(pageID: String, title: String, status: String, source: String, priority: String, dueDate: String, pageURL: String, updatedAt: Date = .now) {
        self.pageID = pageID
        self.title = title
        self.status = status
        self.source = source
        self.priority = priority
        self.dueDate = dueDate
        self.pageURL = pageURL
        self.updatedAt = updatedAt
    }
}

@Model
final class SyncState {
    @Attribute(.unique) var id: String
    var status: String
    var lastAttemptAt: Date?
    var lastSuccessAt: Date?
    var recordCount: Int
    var errorMessage: String

    init(id: String = "notion", status: String = "never", recordCount: Int = 0) {
        self.id = id
        self.status = status
        self.recordCount = recordCount
        self.errorMessage = ""
    }
}

@Model
final class ActivityEvent {
    var timestamp: Date
    var kind: String
    var message: String

    init(kind: String, message: String, timestamp: Date = .now) {
        self.timestamp = timestamp
        self.kind = kind
        self.message = message
    }
}

struct Snapshot: Decodable {
    var generatedAt: String?
    var cards: [SnapshotTask]

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case cards
        case overdue, dueNow = "due_now", dueSoon = "due_soon", noDue = "no_due"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try values.decodeIfPresent(String.self, forKey: .generatedAt)
        if let cards = try values.decodeIfPresent([SnapshotTask].self, forKey: .cards) {
            self.cards = cards
        } else {
            let overdue = try values.decodeIfPresent([SnapshotTask].self, forKey: .overdue) ?? []
            let dueNow = try values.decodeIfPresent([SnapshotTask].self, forKey: .dueNow) ?? []
            let dueSoon = try values.decodeIfPresent([SnapshotTask].self, forKey: .dueSoon) ?? []
            let noDue = try values.decodeIfPresent([SnapshotTask].self, forKey: .noDue) ?? []
            cards = overdue + dueNow + dueSoon + noDue
        }
    }
}

struct SnapshotTask: Codable {
    var pageID: String?
    var title: String?
    var status: String?
    var dbName: String?
    var priority: String?
    var dueDate: String?
    var parsedDue: String?
    var pageURL: String?

    enum CodingKeys: String, CodingKey {
        case pageID = "page_id", title, status
        case dbName = "db_name", priority
        case dueDate = "due_date", parsedDue = "parsed_due"
        case pageURL = "page_url"
    }
}

@MainActor
final class SnapshotImporter: ObservableObject {
    @Published private(set) var lastRefresh: Date?
    @Published private(set) var errorMessage: String?
    @Published private(set) var isRefreshing = false

    func refresh(modelContext: ModelContext) {
        guard !isRefreshing else { return }
        isRefreshing = true
        errorMessage = nil
        do {
            let sync = try syncState(modelContext)
            sync.status = "refreshing"
            sync.lastAttemptAt = .now
            try modelContext.save()
            let result = try runBridge()
            guard result.status == "ok" else {
                throw BridgeError.failed(result.error ?? "Hermes refresh failed")
            }
            let snapshot = try loadSnapshot()
            let existing = try modelContext.fetch(FetchDescriptor<LocalTask>())
            var existingByID = Dictionary(uniqueKeysWithValues: existing.map { ($0.pageID, $0) })
            for item in snapshot.cards {
                guard let pageID = item.pageID, !pageID.isEmpty else { continue }
                if let task = existingByID[pageID] {
                    task.title = item.title ?? task.title
                    task.status = item.status ?? task.status
                    task.source = item.dbName ?? task.source
                    task.priority = item.priority ?? task.priority
                    task.dueDate = item.parsedDue ?? item.dueDate ?? task.dueDate
                    task.pageURL = item.pageURL ?? task.pageURL
                    task.updatedAt = .now
                    existingByID.removeValue(forKey: pageID)
                } else {
                    modelContext.insert(LocalTask(
                        pageID: pageID,
                        title: item.title ?? "Untitled task",
                        status: item.status ?? "",
                        source: item.dbName ?? "Unknown",
                        priority: item.priority ?? "",
                        dueDate: item.parsedDue ?? item.dueDate ?? "",
                        pageURL: item.pageURL ?? ""
                    ))
                }
            }
            for staleTask in existingByID.values {
                modelContext.delete(staleTask)
            }
            try modelContext.save()
            sync.status = "synced"
            sync.lastSuccessAt = .now
            sync.recordCount = snapshot.cards.count
            sync.errorMessage = ""
            modelContext.insert(ActivityEvent(kind: "sync", message: "Imported \(snapshot.cards.count) records from Hermes"))
            try modelContext.save()
            lastRefresh = .now
        } catch {
            errorMessage = error.localizedDescription
            if let sync = try? syncState(modelContext) {
                sync.status = "failed"
                sync.errorMessage = error.localizedDescription
                try? modelContext.save()
                modelContext.insert(ActivityEvent(kind: "error", message: error.localizedDescription))
                try? modelContext.save()
            }
        }
        isRefreshing = false
    }

    private func syncState(_ context: ModelContext) throws -> SyncState {
        if let state = try context.fetch(FetchDescriptor<SyncState>()).first { return state }
        let state = SyncState()
        context.insert(state)
        return state
    }

    private func runBridge() throws -> BridgeResult {
        let process = Process()
        let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["HERMES_PYTHON"] ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".hermes/hermes-agent/venv/bin/python3.11").path)
        guard let bridge = Bundle.main.url(forResource: "hermes_bridge", withExtension: "py") else {
            throw BridgeError.missingBridge
        }
        process.arguments = [bridge.path, "refresh"]
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        process.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let output = String(data: data, encoding: .utf8) ?? ""
        guard let resultData = output.data(using: .utf8), let result = try? JSONDecoder().decode(BridgeResult.self, from: resultData) else {
            throw BridgeError.invalidResponse(output.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return result
    }

    private func loadSnapshot() throws -> Snapshot {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let hermesHome = ProcessInfo.processInfo.environment["HERMES_HOME"]
            .map { URL(fileURLWithPath: $0) }
            ?? home.appendingPathComponent(".hermes/profiles/max-ea")
        let url = hermesHome.appendingPathComponent("notion_tasks_latest.json")
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(Snapshot.self, from: data)
    }
}

private struct BridgeResult: Decodable {
    let status: String
    let count: Int?
    let error: String?
}

private enum BridgeError: LocalizedError {
    case missingBridge
    case invalidResponse(String)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .missingBridge: "Hermes bridge is not bundled with this app"
        case .invalidResponse(let output): "Hermes bridge returned an invalid response: \(output)"
        case .failed(let message): message
        }
    }
}

struct ContentView: View {
    @Environment(\.modelContext) private var modelContext
    @Query(sort: [SortDescriptor<LocalTask>(\.updatedAt, order: .reverse)]) private var tasks: [LocalTask]
    @Query private var syncStates: [SyncState]
    @StateObject private var importer = SnapshotImporter()

    init() {}

    private var activeTasks: [LocalTask] {
        tasks.filter { !["done", "completed", "closed", "canceled", "cancelled"].contains($0.status.lowercased()) }
    }

    var body: some View {
        NavigationSplitView {
            List {
                Label("Now", systemImage: "bolt.fill")
                    .fontWeight(.semibold)
                Label("All Tasks", systemImage: "square.stack.3d.up")
                Label("Activity", systemImage: "clock.arrow.circlepath")
                Divider()
                Text("LOCAL STORE")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Label("\(tasks.count) records", systemImage: "internaldrive")
                    .foregroundStyle(.secondary)
                if let sync = syncStates.first {
                    Label(sync.status.capitalized, systemImage: sync.status == "synced" ? "checkmark.circle" : "exclamationmark.circle")
                        .foregroundStyle(sync.status == "synced" ? .green : .secondary)
                }
            }
            .navigationTitle("SightOps")
            .listStyle(.sidebar)
        } detail: {
            VStack(alignment: .leading, spacing: 0) {
                header
                Divider()
                if activeTasks.isEmpty {
                    ContentUnavailableView("No local records", systemImage: "tray", description: Text("Refresh from Hermes to import the current task snapshot."))
                } else {
                    taskList
                }
                if let error = importer.errorMessage {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .padding(.horizontal, 24)
                        .padding(.bottom, 12)
                }
            }
            .task {
                if tasks.isEmpty { importer.refresh(modelContext: modelContext) }
            }
            .onReceive(NotificationCenter.default.publisher(for: .sightOpsRefreshRequested)) { _ in
                importer.refresh(modelContext: modelContext)
            }
        }
        .tint(Color.cyan)
    }

    private var header: some View {
        HStack(alignment: .bottom) {
            VStack(alignment: .leading, spacing: 4) {
                Text("NOW")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.secondary)
                Text("What’s happening")
                    .font(.system(size: 32, weight: .bold, design: .rounded))
                Text("\(activeTasks.count) active local records")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 8) {
                Button {
                    importer.refresh(modelContext: modelContext)
                } label: {
                    Label(importer.isRefreshing ? "Refreshing…" : "Refresh from Hermes", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.borderedProminent)
                if let lastRefresh = importer.lastRefresh {
                    Text("Imported \(lastRefresh.formatted(date: .omitted, time: .shortened))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(24)
    }

    private var taskList: some View {
        List(activeTasks) { task in
            HStack(spacing: 14) {
                Circle()
                    .fill(statusColor(task.status))
                    .frame(width: 9, height: 9)
                VStack(alignment: .leading, spacing: 5) {
                    Text(task.title)
                        .font(.headline)
                    HStack(spacing: 10) {
                        Text(task.source.uppercased())
                        Text(task.status.isEmpty ? "NO STATUS" : task.status.uppercased())
                        if !task.dueDate.isEmpty { Text(task.dueDate) }
                    }
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                }
                Spacer()
                if !task.priority.isEmpty {
                    Text(task.priority.uppercased())
                        .font(.caption2.monospaced().weight(.bold))
                        .foregroundStyle(task.priority.lowercased().contains("high") ? .primary : .secondary)
                }
                if let url = URL(string: task.pageURL), !task.pageURL.isEmpty {
                    Link(destination: url) { Image(systemName: "arrow.up.right.square") }
                }
            }
            .padding(.vertical, 6)
        }
        .listStyle(.inset)
    }

    private func statusColor(_ status: String) -> Color {
        switch status.lowercased() {
        case "in progress", "doing", "active": .cyan
        case "waiting", "blocked": .pink
        case "to review", "in review": .purple
        case "not started", "ready", "up next": .blue
        default: .gray
        }
    }
}
