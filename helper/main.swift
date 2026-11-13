// WAExport: the only component that holds Full Disk Access.
//
// It snapshots the WhatsApp for Mac databases without writing to WhatsApp's
// container and writes one file, wa.sqlite, with:
//   - an index of every chat (names, unread counts, dates; no message text),
//   - group members, push names and address-book names (for sender names),
//   - the messages of the chats listed in selection.json (written by the
//     Python side from config/policy.toml), within the requested window.
// No network, no child processes, fixed input/output locations.

import Foundation
import SQLite3

let helperVersion = "0.2.1"
let sqliteTransient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

struct Failure: Error {
    let kind: String
    let message: String
}

func errorMessage(_ db: OpaquePointer?) -> String {
    db.map { String(cString: sqlite3_errmsg($0)) } ?? "unknown"
}

func exec(_ db: OpaquePointer?, _ sql: String) throws {
    var err: UnsafeMutablePointer<CChar>?
    if sqlite3_exec(db, sql, nil, nil, &err) != SQLITE_OK {
        let msg = err.map { String(cString: $0) } ?? errorMessage(db)
        sqlite3_free(err)
        throw Failure(kind: "sql", message: "\(msg) [\(sql.prefix(60))]")
    }
}

/// Runs one statement with text/double bindings.
func run(_ db: OpaquePointer?, _ sql: String, _ args: [Any] = []) throws {
    var stmt: OpaquePointer?
    guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
        throw Failure(kind: "sql", message: "\(errorMessage(db)) [\(sql.prefix(60))]")
    }
    defer { sqlite3_finalize(stmt) }
    for (i, arg) in args.enumerated() {
        let idx = Int32(i + 1)
        switch arg {
        case let s as String: sqlite3_bind_text(stmt, idx, s, -1, sqliteTransient)
        case let d as Double: sqlite3_bind_double(stmt, idx, d)
        default: sqlite3_bind_null(stmt, idx)
        }
    }
    let rc = sqlite3_step(stmt)
    guard rc == SQLITE_DONE || rc == SQLITE_ROW else {
        throw Failure(kind: "sql", message: "\(errorMessage(db)) [\(sql.prefix(60))]")
    }
}

func scalar(_ db: OpaquePointer?, _ sql: String) -> Int64 {
    var stmt: OpaquePointer?
    defer { sqlite3_finalize(stmt) }
    guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK, sqlite3_step(stmt) == SQLITE_ROW else { return -1 }
    return sqlite3_column_int64(stmt, 0)
}

func columns(_ db: OpaquePointer?, _ schema: String, _ table: String) -> Set<String> {
    var stmt: OpaquePointer?
    defer { sqlite3_finalize(stmt) }
    var result = Set<String>()
    guard sqlite3_prepare_v2(db, "PRAGMA \(schema).table_info(\(table))", -1, &stmt, nil) == SQLITE_OK else { return result }
    while sqlite3_step(stmt) == SQLITE_ROW {
        result.insert(String(cString: sqlite3_column_text(stmt, 1)))
    }
    return result
}

/// Column list of `main.table` with the `redacting` columns projected as NULL, so their values are never written.
func projection(_ db: OpaquePointer?, _ table: String, redacting: Set<String>) -> String {
    columns(db, "main", table).sorted().map { redacting.contains($0) ? "NULL AS \"\($0)\"" : "\"\($0)\"" }
        .joined(separator: ", ")
}

func require(_ db: OpaquePointer?, _ schema: String, _ table: String, _ needed: [String]) throws {
    let missing = Set(needed).subtracting(columns(db, schema, table))
    if !missing.isEmpty {
        throw Failure(kind: "schema", message: "\(table) is missing columns: \(missing.sorted().joined(separator: ", "))")
    }
}

/// Distinguishes "no Full Disk Access" from other failures before SQLite hides the errno.
func preflight(_ path: String) throws {
    let fd = open(path, O_RDONLY)
    if fd < 0 {
        let code = errno
        let kind = (code == EPERM || code == EACCES) ? "fda_denied" : "source_missing"
        throw Failure(kind: kind, message: "\(path): \(String(cString: strerror(code)))")
    }
    close(fd)
}

/// Copies a live WAL database into `schema` of `dest`. readonly_shm keeps the
/// connection from writing read-marks into WhatsApp's -shm; without -wal/-shm
/// there is no WAL content to miss, so immutable is safe.
func snapshot(_ path: String, into dest: OpaquePointer?, schema: String) throws {
    let fm = FileManager.default
    let hasWal = fm.fileExists(atPath: path + "-wal") && fm.fileExists(atPath: path + "-shm")
    let encoded = path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? path
    let uri = "file:\(encoded)?mode=ro&" + (hasWal ? "readonly_shm=1" : "immutable=1")

    var src: OpaquePointer?
    defer { sqlite3_close(src) }
    guard sqlite3_open_v2(uri, &src, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, nil) == SQLITE_OK else {
        throw Failure(kind: "open", message: "\(path): \(errorMessage(src))")
    }
    sqlite3_busy_timeout(src, 2000)

    // An in-memory destination only accepts a backup with the same page size.
    let pageSize = scalar(src, "PRAGMA page_size")
    if pageSize > 0 { try exec(dest, "PRAGMA \(schema).page_size = \(pageSize)") }

    guard let backup = sqlite3_backup_init(dest, schema, src, "main") else {
        throw Failure(kind: "backup", message: "\(path): \(errorMessage(dest))")
    }
    var rc = sqlite3_backup_step(backup, -1)
    var tries = 0
    while (rc == SQLITE_BUSY || rc == SQLITE_LOCKED) && tries < 25 {
        usleep(200_000)
        rc = sqlite3_backup_step(backup, -1)
        tries += 1
    }
    sqlite3_backup_finish(backup)
    guard rc == SQLITE_DONE else {
        throw Failure(kind: "backup", message: "\(path): SQLite code \(rc)")
    }
}

struct Selection: Decodable {
    var jids: [String] = []
    var since: Double = 0  // Core Data seconds (since 2001-01-01 UTC)
}

func export(source: String, data: String) throws -> [String: Any] {
    let chatStorage = source + "/ChatStorage.sqlite"
    try preflight(chatStorage)

    var mem: OpaquePointer?
    defer { sqlite3_close(mem) }
    guard sqlite3_open_v2(":memory:", &mem, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_URI, nil) == SQLITE_OK else {
        throw Failure(kind: "open", message: "memory: \(errorMessage(mem))")
    }
    try exec(mem, "ATTACH ':memory:' AS contacts; ATTACH ':memory:' AS lid;")
    try snapshot(chatStorage, into: mem, schema: "main")
    for (file, schema) in [("ContactsV2.sqlite", "contacts"), ("LID.sqlite", "lid")]
    where FileManager.default.fileExists(atPath: source + "/" + file) {
        try snapshot(source + "/" + file, into: mem, schema: schema)
    }

    try require(mem, "main", "ZWACHATSESSION", ["Z_PK", "ZCONTACTJID", "ZPARTNERNAME", "ZLASTMESSAGEDATE"])
    try require(mem, "main", "ZWAMESSAGE", ["Z_PK", "ZCHATSESSION", "ZMESSAGEDATE"])
    try require(mem, "main", "ZWAMEDIAITEM", ["ZMESSAGE"])

    var selection = Selection()
    if let raw = FileManager.default.contents(atPath: data + "/selection.json") {
        selection = (try? JSONDecoder().decode(Selection.self, from: raw)) ?? Selection()
    }
    let jidsJSON = String(data: try JSONEncoder().encode(selection.jids), encoding: .utf8) ?? "[]"

    let tmp = data + "/wa.sqlite.\(getpid()).tmp"
    unlink(tmp)  // removes a stale file or a planted symlink; never follows it
    var renamed = false
    defer { if !renamed { unlink(tmp) } }
    try run(mem, "ATTACH ? AS out", [tmp])
    try exec(mem, "BEGIN")
    // The index covers every chat, so message text and drafts are never written, not even to freed pages.
    // Pinned chats carry a far-future ZLASTMESSAGEDATE (it drives sorting), so keep the real one.
    let lastDate = columns(mem, "main", "ZWACHATSESSION").contains("ZLASTMESSAGE")
        ? "(SELECT m.ZMESSAGEDATE FROM main.ZWAMESSAGE m WHERE m.Z_PK = c.ZLASTMESSAGE)" : "CAST(NULL AS REAL)"
    try exec(mem, """
        CREATE TABLE out.chats AS SELECT \(projection(mem, "ZWACHATSESSION", redacting: ["ZLASTMESSAGETEXT", "ZSAVEDINPUT"])),
            \(lastDate) AS last_message_date FROM main.ZWACHATSESSION c
        """)
    for (table, source) in [("group_members", "ZWAGROUPMEMBER"), ("push_names", "ZWAPROFILEPUSHNAME")]
    where !columns(mem, "main", source).isEmpty {
        try exec(mem, "CREATE TABLE out.\(table) AS SELECT * FROM main.\(source)")
    }
    if columns(mem, "contacts", "ZWAADDRESSBOOKCONTACT").isSuperset(of: ["ZFULLNAME", "ZWHATSAPPID", "ZLID"]) {
        try exec(mem, "CREATE TABLE out.contacts AS SELECT ZFULLNAME, ZWHATSAPPID, ZLID FROM contacts.ZWAADDRESSBOOKCONTACT")
    }
    if columns(mem, "lid", "ZWAZACCOUNT").isSuperset(of: ["ZIDENTIFIER", "ZPHONENUMBER", "ZDISPLAYNAME"]) {
        try exec(mem, "CREATE TABLE out.lid_accounts AS SELECT ZIDENTIFIER, ZPHONENUMBER, ZDISPLAYNAME FROM lid.ZWAZACCOUNT")
    }
    try run(mem, """
        CREATE TABLE out.messages AS SELECT * FROM main.ZWAMESSAGE
        WHERE ZMESSAGEDATE >= ?2 AND ZCHATSESSION IN (
            SELECT Z_PK FROM main.ZWACHATSESSION WHERE ZCONTACTJID IN (SELECT value FROM json_each(?1)))
        """, [jidsJSON, selection.since])
    // CDN URL + key would let anyone fetch and decrypt the attachment.
    try exec(mem, """
        CREATE TABLE out.media AS SELECT \(projection(mem, "ZWAMEDIAITEM", redacting: ["ZMEDIAURL", "ZMEDIAKEY"]))
        FROM main.ZWAMEDIAITEM WHERE ZMESSAGE IN (SELECT Z_PK FROM out.messages)
        """)
    // CREATE TABLE AS drops indexes; these serve the store's per-chat queries and joins.
    try exec(mem, "CREATE INDEX out.messages_chat_date ON messages (ZCHATSESSION, ZMESSAGEDATE)")
    try exec(mem, "CREATE INDEX out.media_pk ON media (Z_PK)")
    if columns(mem, "out", "group_members").contains("Z_PK") {
        try exec(mem, "CREATE INDEX out.group_members_pk ON group_members (Z_PK)")
    }
    try exec(mem, "CREATE TABLE out.meta (key TEXT PRIMARY KEY, value TEXT)")
    let exportedAt = ISO8601DateFormatter().string(from: Date())
    for (key, value) in [("exported_at", exportedAt), ("since", String(selection.since)),
                         ("helper_version", helperVersion), ("sqlite_version", String(cString: sqlite3_libversion()))] {
        try run(mem, "INSERT INTO out.meta VALUES (?, ?)", [key, value])
    }
    try exec(mem, "COMMIT")
    let chats = scalar(mem, "SELECT count(*) FROM out.chats")
    let messages = scalar(mem, "SELECT count(*) FROM out.messages")
    try exec(mem, "DETACH out")

    guard rename(tmp, data + "/wa.sqlite") == 0 else {
        throw Failure(kind: "write", message: "rename: \(String(cString: strerror(errno)))")
    }
    renamed = true
    return ["chats": chats, "messages": messages, "selected": selection.jids.count, "exported_at": exportedAt]
}

func writeStatus(_ status: [String: Any], to data: String) {
    if let json = try? JSONSerialization.data(withJSONObject: status, options: [.prettyPrinted, .sortedKeys]) {
        try? json.write(to: URL(fileURLWithPath: data + "/status.json"), options: .atomic)
    }
}

// MARK: - main

umask(0o077)
let home = FileManager.default.homeDirectoryForCurrentUser.path
let defaultSource = home + "/Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
let defaultData = home + "/.local/share/whatsapp-context"
var source = defaultSource
var data = defaultData
var runID = ""
var args = Array(CommandLine.arguments.dropFirst())
while let arg = args.first {
    args.removeFirst()
    switch arg {
    case "--source": source = args.isEmpty ? source : args.removeFirst()
    case "--data": data = args.isEmpty ? data : args.removeFirst()
    case "--run": runID = args.isEmpty ? "" : args.removeFirst()
    case "--version": print(helperVersion); exit(0)
    default: break  // "export" is the only verb
    }
}

/// Resolves symlinks in the parent so a path can be compared before it exists.
func canonical(_ path: String) -> String {
    let url = URL(fileURLWithPath: path).standardizedFileURL
    return url.deletingLastPathComponent().resolvingSymlinksInPath().appendingPathComponent(url.lastPathComponent).path
}

/// This process holds Full Disk Access and anyone can launch it with arguments, so only the
/// default locations, or paths under the user's temporary directory (tests), are accepted.
func allowed(_ path: String, _ defaultPath: String) -> Bool {
    let temp = URL(fileURLWithPath: NSTemporaryDirectory()).resolvingSymlinksInPath().path
    let real = canonical(path)
    return real == canonical(defaultPath) || real.hasPrefix(temp + "/")
}

func refuse(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

if !allowed(data, defaultData) { refuse("data dir not allowed: \(data)") }
if !allowed(source, defaultSource) { refuse("source dir not allowed: \(source)") }
var info = stat()
if lstat(data, &info) == 0 && (info.st_mode & S_IFMT) == S_IFLNK { refuse("data dir is a symlink: \(data)") }
try? FileManager.default.createDirectory(atPath: data, withIntermediateDirectories: true,
                                         attributes: [.posixPermissions: 0o700])

var status: [String: Any] = ["run": runID, "helper_version": helperVersion,
                             "sqlite_version": String(cString: sqlite3_libversion())]
do {
    let result = try export(source: source, data: data)
    status.merge(result) { _, new in new }
    status["ok"] = true
    writeStatus(status, to: data)
    exit(0)
} catch let failure as Failure {
    status["ok"] = false
    status["error_kind"] = failure.kind
    status["error"] = failure.message
    status["failed_at"] = ISO8601DateFormatter().string(from: Date())
    writeStatus(status, to: data)
    exit(failure.kind == "fda_denied" ? 77 : 1)
} catch {
    status["ok"] = false
    status["error_kind"] = "other"
    status["error"] = "\(error)"
    writeStatus(status, to: data)
    exit(1)
}
