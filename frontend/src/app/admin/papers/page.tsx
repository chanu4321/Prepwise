"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { AccessNotice } from "@/components/AccessNotice";
import { AdminNav } from "@/components/AdminNav";
import { apiFetch, describeApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

type Status = "review" | "live" | "rejected" | "failed" | "processing";

type Reason =
    | { code: "unreadable"; letters: number }
    | { code: "not_exam_paper" }
    | { code: "missing_details"; fields: string[] }
    | { code: "possible_duplicate"; paperId: number; overlap: number };

type AdminPaper = {
    id: number;
    filename: string;
    status: Status;
    note: string | null;
    reasons: Reason[];
    subjectCode: string | null;
    subjectName: string | null;
    semester: string | null;
    year: string | null;
    time: string | null;
    marks: string | null;
    uploadedAt: string | null;
    uploader: { name: string | null; email: string | null } | null;
    reviewedAt: string | null;
    preview: string;
};

type Listing = { papers: AdminPaper[]; counts: Record<Status, number>; matches: Record<string, AdminPaper> };

const TABS: { status: Status; label: string }[] = [
    { status: "review", label: "Review" },
    { status: "live", label: "Live" },
    { status: "rejected", label: "Rejected" },
    { status: "failed", label: "Failed" },
    { status: "processing", label: "Processing" },
];

const DETAIL_FIELDS = [
    ["subjectCode", "Subject code"],
    ["subjectName", "Subject name"],
    ["semester", "Semester"],
    ["year", "Session"],
    ["time", "Time"],
    ["marks", "Marks"],
] as const;

const FIELD_LABEL: Record<string, string> = { subject_code: "subject code", subject_name: "subject name", year: "session" };

function describeReason(reason: Reason): string {
    switch (reason.code) {
        case "unreadable":
            return `Very little readable text (${reason.letters} letters).`;
        case "not_exam_paper":
            return "Doesn't look like an exam paper (no marks, time or exam wording).";
        case "missing_details":
            return `Couldn't read: ${reason.fields.map(f => FIELD_LABEL[f] ?? f).join(", ")}.`;
        case "possible_duplicate":
            return `Looks like a copy of paper #${reason.paperId} (${Math.round(reason.overlap * 100)}% same questions).`;
    }
}

async function openPdf(id: number) {
    const response = await apiFetch(`/api/v1/admin/papers/${id}/file`);
    const url = URL.createObjectURL(await response.blob());
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function Details({ paper }: { paper: AdminPaper }) {
    return (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
            {DETAIL_FIELDS.map(([key, label]) => (
                <div key={key}>
                    <dt className="text-xs text-muted-foreground">{label}</dt>
                    <dd>{paper[key] ?? "–"}</dd>
                </div>
            ))}
        </dl>
    );
}

function PaperCard({ paper, matches, onChanged, onError }: {
    paper: AdminPaper;
    matches: Record<string, AdminPaper>;
    onChanged: () => Promise<void>;
    onError: (message: string) => void;
}) {
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState<Record<string, string>>({});
    const [busy, setBusy] = useState(false);

    const run = async (action: () => Promise<unknown>) => {
        setBusy(true);
        try {
            await action();
            await onChanged();
        } catch (e) {
            onError(describeApiError(e));
        } finally {
            setBusy(false);
        }
    };

    const post = (status: "live" | "rejected" | "processing", note?: string) =>
        run(() => apiFetch(`/api/v1/admin/papers/${paper.id}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status, note }),
        }));

    const askReasonThen = (status: "rejected") => {
        const note = window.prompt("Reason (the uploader will see this):")?.trim();
        if (note) void post(status, note);
    };

    const startEdit = () => {
        setDraft({
            filename: paper.filename,
            ...Object.fromEntries(DETAIL_FIELDS.map(([key]) => [key, paper[key] ?? ""])),
        });
        setEditing(true);
    };

    const save = () => {
        const current: Record<string, string> = {
            filename: paper.filename,
            ...Object.fromEntries(DETAIL_FIELDS.map(([key]) => [key, paper[key] ?? ""])),
        };
        const changes = Object.fromEntries(Object.entries(draft).filter(([key, value]) => value !== current[key]));
        if (Object.keys(changes).length === 0) {
            setEditing(false);
            return;
        }
        void run(async () => {
            await apiFetch(`/api/v1/admin/papers/${paper.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(changes),
            });
            setEditing(false);
        });
    };

    const remove = () => {
        if (window.confirm(`Delete "${paper.filename}" permanently? Its file and search entry are removed too.`)) {
            void run(() => apiFetch(`/api/v1/admin/papers/${paper.id}`, { method: "DELETE" }));
        }
    };

    const copies = paper.reasons.filter((r): r is Extract<Reason, { code: "possible_duplicate" }> => r.code === "possible_duplicate");
    const button = "rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted/40 disabled:opacity-50";

    return (
        <article className="rounded-xl border border-border p-4 sm:p-5">
            <div className="flex flex-col gap-4 lg:flex-row">
                <div className="flex-1 min-w-0 space-y-3">
                    <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        <h2 className="font-semibold break-all">#{paper.id} {paper.filename}</h2>
                        <span className="text-xs text-muted-foreground">
                            {paper.uploader ? paper.uploader.name ?? paper.uploader.email : "Anonymous"}
                            {paper.uploadedAt && ` · ${new Date(paper.uploadedAt).toLocaleString()}`}
                        </span>
                    </header>

                    {paper.reasons.length > 0 && (
                        <ul className="list-disc pl-5 text-sm text-amber-400">
                            {paper.reasons.map((reason, i) => <li key={i}>{describeReason(reason)}</li>)}
                        </ul>
                    )}
                    {paper.note && <p className="text-sm text-muted-foreground">Note: {paper.note}</p>}

                    {editing ? (
                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                            {[["filename", "File name"] as const, ...DETAIL_FIELDS].map(([key, label]) => (
                                <label key={key} className="text-xs text-muted-foreground">
                                    {label}
                                    <input
                                        value={draft[key] ?? ""}
                                        onChange={e => setDraft(d => ({ ...d, [key]: e.target.value }))}
                                        className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
                                    />
                                </label>
                            ))}
                        </div>
                    ) : (
                        <Details paper={paper} />
                    )}

                    {paper.preview && (
                        <details className="text-sm">
                            <summary className="cursor-pointer text-muted-foreground">Questions (OCR)</summary>
                            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted/30 p-3 text-xs">{paper.preview}</pre>
                        </details>
                    )}

                    <div className="flex flex-wrap gap-2">
                        <button className={button} disabled={busy} onClick={() => void openPdf(paper.id).catch(e => onError(describeApiError(e)))}>Open PDF</button>
                        {editing ? (
                            <>
                                <button className={button} disabled={busy} onClick={save}>Save</button>
                                <button className={button} disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
                            </>
                        ) : (
                            paper.status !== "processing" && <button className={button} disabled={busy} onClick={startEdit}>Edit</button>
                        )}
                        {paper.status === "review" && (
                            <>
                                <button className={cn(button, "border-green-600 text-green-500")} disabled={busy} onClick={() => void post("live")}>Approve</button>
                                <button className={cn(button, "border-red-600 text-red-400")} disabled={busy} onClick={() => askReasonThen("rejected")}>Reject</button>
                            </>
                        )}
                        {paper.status === "live" && (
                            <button className={cn(button, "border-red-600 text-red-400")} disabled={busy} onClick={() => askReasonThen("rejected")}>Take down</button>
                        )}
                        {paper.status === "rejected" && <button className={button} disabled={busy} onClick={() => void post("live")}>Restore</button>}
                        {paper.status === "failed" && <button className={button} disabled={busy} onClick={() => void post("processing")}>Retry</button>}
                        {paper.status !== "processing" && (
                            <button className={cn(button, "text-red-400")} disabled={busy} onClick={remove}>Delete</button>
                        )}
                    </div>
                </div>

                {copies.map(copy => {
                    const match = matches[String(copy.paperId)];
                    if (!match) return null;
                    return (
                        <aside key={copy.paperId} className="lg:w-96 shrink-0 space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
                            <p className="text-xs uppercase tracking-wider text-amber-400">
                                Possible copy of · {Math.round(copy.overlap * 100)}% same questions
                            </p>
                            <h3 className="font-medium break-all">#{match.id} {match.filename} <span className="text-xs text-muted-foreground">({match.status})</span></h3>
                            <Details paper={match} />
                            {match.preview && (
                                <details className="text-sm">
                                    <summary className="cursor-pointer text-muted-foreground">Questions (OCR)</summary>
                                    <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted/30 p-3 text-xs">{match.preview}</pre>
                                </details>
                            )}
                            <button className={button} onClick={() => void openPdf(match.id).catch(e => onError(describeApiError(e)))}>Open PDF</button>
                        </aside>
                    );
                })}
            </div>
        </article>
    );
}

export default function AdminPapersPage() {
    const { ready, me } = useAuth();
    const [tab, setTab] = useState<Status>("review");
    const [listing, setListing] = useState<Listing | null>(null);
    const [error, setError] = useState("");
    const isAdmin = me?.role === "admin";

    const load = useCallback(async () => {
        try {
            const response = await apiFetch(`/api/v1/admin/papers?status=${tab}`);
            setListing((await response.json()) as Listing);
            setError("");
        } catch (e) {
            setError(describeApiError(e));
        }
    }, [tab]);

    useEffect(() => {
        if (!isAdmin) return;

        void (async () => {
            await load();
        })();
    }, [isAdmin, load]);

    if (!ready) return null;
    if (!isAdmin) return <AccessNotice title="Admins only" message="This page is for PrepWise admins." showSignIn={!me} />;

    return (
        <div className="container mx-auto max-w-6xl px-4 py-12">
            <AdminNav />
            <h1 className="mb-6 text-3xl font-bold">Papers</h1>
            <div className="mb-6 flex flex-wrap gap-2">
                {TABS.map(t => (
                    <button
                        key={t.status}
                        onClick={() => setTab(t.status)}
                        className={cn(
                            "rounded-full border px-3 py-1 text-sm",
                            tab === t.status ? "border-primary bg-primary/15 text-primary" : "border-border text-muted-foreground hover:text-foreground",
                        )}
                    >
                        {t.label} {listing ? `(${listing.counts[t.status] ?? 0})` : ""}
                    </button>
                ))}
            </div>
            {error && <p className="mb-4 text-sm text-red-400">{error}</p>}
            {listing && listing.papers.length === 0 && <p className="text-muted-foreground">Nothing here.</p>}
            <div className="space-y-4">
                {listing?.papers.map(paper => (
                    <PaperCard key={paper.id} paper={paper} matches={listing.matches} onChanged={load} onError={setError} />
                ))}
            </div>
        </div>
    );
}
