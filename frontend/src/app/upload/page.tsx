"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useAuth } from "@/components/AuthProvider";
import { AccessNotice } from "@/components/AccessNotice";
import { apiFetch, describeApiError, ApiRequestError } from "@/lib/api";
import { CloudUpload, X, FileText, CheckCircle, AlertCircle, Clock, ExternalLink } from "lucide-react";
import { cn, API_BASE_URL } from "@/lib/utils";

type PaperStatus = "processing" | "live" | "review" | "rejected" | "failed";

type UploadTask = {
    id: string;
    file: File;
    progress: number;
    status: "uploading" | "error" | PaperStatus;
    uploadKey?: string;
    paperId?: number | null;
    message?: string;
    startedAt?: number;
    gaveUp?: boolean;
};

type UploadStatus = { id: number; filename: string; status: PaperStatus; note: string | null; uploadedAt: string | null };

const MAX_BYTES = 10 * 1024 * 1024;
const POLL_MS = 5000;
const GIVE_UP_MS = 15 * 60 * 1000;

const STATUS_LABEL: Record<PaperStatus, string> = {
    processing: "Processing…",
    live: "Live",
    review: "Under review",
    rejected: "Rejected",
    failed: "Failed",
};

const STATUS_CLASS: Record<PaperStatus, string> = {
    processing: "text-amber-400",
    live: "text-green-500",
    review: "text-sky-400",
    rejected: "text-red-400",
    failed: "text-red-400",
};

const isPdf = (f: File) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
const paperUrl = (id: number) => `${API_BASE_URL}/api/v1/documents/${id}/download`;

export default function UploadPage() {
    const { ready, me, refresh } = useAuth();
    const [isDragging, setIsDragging] = useState(false);
    const [tasks, setTasks] = useState<UploadTask[]>([]);
    const [myUploads, setMyUploads] = useState<UploadStatus[]>([]);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const tasksRef = useRef<UploadTask[]>([]);
    useEffect(() => { tasksRef.current = tasks; }, [tasks]);

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    };

    const handleDragLeave = () => {
        setIsDragging(false);
    };

    const updateTask = useCallback((id: string, change: Partial<UploadTask>) => {
        setTasks(prev => prev.map(t => (t.id === id ? { ...t, ...change } : t)));
    }, []);

    const loadMyUploads = useCallback(async () => {
        if (!me) return;
        try {
            const response = await apiFetch("/api/v1/me/uploads");
            setMyUploads(((await response.json()) as { uploads: UploadStatus[] }).uploads);
        } catch {
            // the list is a convenience; the per-file status above still works
        }
    }, [me]);

    useEffect(() => {
        void (async () => {
            await loadMyUploads();
        })();
    }, [loadMyUploads]);

    const processFiles = useCallback((selectedFiles: File[]) => {
        const newTasks: UploadTask[] = selectedFiles.map(file => ({
            id: Math.random().toString(36).substring(7),
            file,
            progress: 0,
            status: file.size > MAX_BYTES ? "error" : "uploading",
            message: file.size > MAX_BYTES ? "This file is over 10 MB. Compress it or split it, then try again." : undefined,
        }));
        setTasks(prev => [...prev, ...newTasks]);

        newTasks.filter(task => task.status === "uploading").forEach(task => {
            let currentProgress = 0;
            const progressInterval = setInterval(() => {
                currentProgress = Math.min(90, currentProgress + Math.floor(Math.random() * 15) + 5);
                setTasks(prev => prev.map(t =>
                    t.id === task.id && t.status === "uploading" ? { ...t, progress: currentProgress } : t
                ));
            }, 300);

            const formData = new FormData();
            formData.append("file", task.file);

            apiFetch("/api/v1/documents/ingest", { method: "POST", body: formData })
                .then(async response => {
                    const body = (await response.json()) as { id: number; uploadKey: string; status: PaperStatus };
                    clearInterval(progressInterval);
                    updateTask(task.id, { progress: 100, status: body.status, uploadKey: body.uploadKey,
                                          paperId: body.id, startedAt: Date.now() });
                    void refresh();
                    void loadMyUploads();
                })
                .catch(err => {
                    clearInterval(progressInterval);
                    const duplicate = err instanceof ApiRequestError && err.code === "duplicate_paper";
                    updateTask(task.id, {
                        status: "error",
                        message: describeApiError(err),
                        paperId: duplicate && err.details.paperStatus === "live" ? Number(err.details.paperId) : null,
                    });
                });
        });
    }, [refresh, loadMyUploads, updateTask]);

    const polling = tasks.some(t => t.status === "processing" && !t.gaveUp);

    useEffect(() => {
        if (!polling) return;
        const timer = setInterval(() => {
            for (const task of tasksRef.current) {
                if (task.status !== "processing" || task.gaveUp || !task.uploadKey) continue;
                if (Date.now() - (task.startedAt ?? Date.now()) > GIVE_UP_MS) {
                    updateTask(task.id, { gaveUp: true, message: "Still processing. Check back later." });
                    continue;
                }
                apiFetch(`/api/v1/documents/uploads/${task.uploadKey}`)
                    .then(async response => {
                        const status = (await response.json()) as UploadStatus;
                        if (status.status !== "processing") {
                            updateTask(task.id, { status: status.status, message: status.note ?? undefined });
                            void loadMyUploads();
                        }
                    })
                    .catch(() => {
                        // try again on the next tick
                    });
            }
        }, POLL_MS);
        return () => clearInterval(timer);
    }, [polling, updateTask, loadMyUploads]);

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        const droppedFiles = Array.from(e.dataTransfer.files).filter(isPdf);
        if (droppedFiles.length > 0) processFiles(droppedFiles);
    };

    const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files.length > 0) {
            const selectedFiles = Array.from(e.target.files).filter(isPdf);
            processFiles(selectedFiles);
        }
    };

    const removeTask = (id: string) => {
        setTasks(prev => prev.filter(t => t.id !== id));
    };

    if (!ready) return null;
    if (me?.role === "faculty" && !me.verified) {
        return <AccessNotice title="Paper uploads aren't available on a faculty trial"
            message="Trial faculty accounts can generate mock papers. An admin can verify your account to enable uploads." />;
    }
    const allowance = !me
        ? "Uploading anonymously: 5 papers a day. Sign in as a student for 20."
        : me.limits.upload === null
            ? "Unlimited uploads."
            : `${me.usedToday.upload} of ${me.limits.upload} uploads used today.`;

    return (
        <div className="min-h-[calc(100vh-56px)] bg-background text-foreground overflow-x-hidden pt-16 pb-24">

            {/* Ambient Background Glows */}
            <div className="fixed top-20 right-20 w-[500px] h-[500px] bg-cyan-500/5 rounded-full blur-[120px] pointer-events-none" />
            <div className="fixed bottom-20 left-20 w-[400px] h-[400px] bg-indigo-500/5 rounded-full blur-[120px] pointer-events-none" />

            <div className="container mx-auto max-w-4xl px-4 relative">
                {/* Heading */}
                <div className="mb-10 lg:mb-12">
                    <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold tracking-tight mb-4">
                        Upload <span className="bg-gradient-to-r from-cyan-400 to-purple-500 bg-clip-text text-transparent">Papers</span>
                    </h1>
                    <p className="text-muted-foreground text-lg max-w-xl leading-relaxed">
                        Contribute to the academic community and help fellow students excel.
                    </p>
                    <p className="mt-3 text-sm text-primary">{allowance}</p>
                </div>

                {/* Dropzone */}
                <div
                    className={cn(
                        "relative flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-20 transition-all duration-200",
                        isDragging
                            ? "border-indigo-400 bg-indigo-500/10 scale-[1.01]"
                            : "border-indigo-500/20 bg-[#0f121b] hover:bg-[#131620]"
                    )}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                >
                    <input
                        type="file"
                        ref={fileInputRef}
                        className="hidden"
                        onChange={handleFileSelect}
                        multiple
                        accept=".pdf,application/pdf"
                    />

                    <div className="mb-6 h-20 w-20 rounded-full bg-[#171b29] flex items-center justify-center shadow-inner">
                        <CloudUpload className={cn("h-10 w-10 text-cyan-400 transition-transform duration-300", isDragging && "scale-110 -translate-y-1")} />
                    </div>

                    <h3 className="text-2xl font-bold mb-3">Drag and drop your papers</h3>
                    <p className="text-muted-foreground text-sm mb-8 font-medium">
                        PDF only · up to 10 MB and 20 pages per file
                    </p>

                    <button
                        onClick={() => fileInputRef.current?.click()}
                        className="bg-indigo-600 hover:bg-indigo-500 text-white px-8 py-3 rounded-lg font-semibold transition-all duration-200 shadow-lg shadow-indigo-500/25 hover:shadow-indigo-500/40 hover:-translate-y-0.5 active:translate-y-0"
                    >
                        Browse Files
                    </button>
                </div>

                {/* Recent Uploads */}
                {tasks.length > 0 && (
                    <div className="mt-8 rounded-2xl bg-[#131620] border border-white/5 p-6 sm:p-8 shadow-xl">
                        <h4 className="text-xs font-semibold text-muted-foreground tracking-widest uppercase mb-6">Recent Uploads</h4>

                        <div className="space-y-4">
                            {tasks.map((task) => (
                                <div key={task.id} className="flex flex-col sm:flex-row sm:items-center gap-4 bg-[#0a0c12] rounded-xl p-4 sm:p-5 border border-white/5 transition-all hover:bg-black/40">
                                    <div className="flex items-center gap-4 flex-1 min-w-0">
                                        <div className="h-12 w-12 rounded-lg bg-cyan-500/10 flex items-center justify-center shrink-0">
                                            <FileText className="h-6 w-6 text-cyan-400" />
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex justify-between items-center mb-2.5">
                                                <span className="text-sm font-semibold truncate pr-4 text-foreground/90">{task.file.name}</span>
                                                <span className="text-xs font-medium tabular-nums">
                                                    {task.status === "uploading" && <span className="text-muted-foreground">{task.progress}%</span>}
                                                    {task.status === "error" && <span className="text-red-500 flex items-center gap-1"><AlertCircle className="h-3 w-3"/> Not uploaded</span>}
                                                    {task.status !== "uploading" && task.status !== "error" && (
                                                        <span className={cn("flex items-center gap-1", STATUS_CLASS[task.status])}>
                                                            {task.status === "processing" ? <Clock className="h-3 w-3"/> : task.status === "live" ? <CheckCircle className="h-3 w-3"/> : <AlertCircle className="h-3 w-3"/>}
                                                            {STATUS_LABEL[task.status]}
                                                        </span>
                                                    )}
                                                </span>
                                            </div>
                                            {/* Progress bar container */}
                                            <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden">
                                                <div
                                                    className={cn("h-full rounded-full transition-all duration-300 ease-out",
                                                        task.status === "uploading" && "bg-gradient-to-r from-cyan-400 to-emerald-400",
                                                        task.status === "processing" && "bg-amber-400/70 animate-pulse w-full",
                                                        (task.status === "live") && "bg-green-500 w-full",
                                                        task.status === "review" && "bg-sky-400 w-full",
                                                        (task.status === "rejected" || task.status === "failed" || task.status === "error") && "bg-red-500 w-full")}
                                                    style={task.status === "uploading" ? { width: `${task.progress}%` } : undefined}
                                                />
                                            </div>
                                            {task.message && <p className={cn("mt-2 text-xs", task.status === "error" || task.status === "rejected" || task.status === "failed" ? "text-red-400" : "text-muted-foreground")}>{task.message}</p>}
                                            {task.paperId != null && (task.status === "live" || task.status === "error") && (
                                                <a href={paperUrl(task.paperId)} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs text-cyan-400 hover:underline">
                                                    Open the paper <ExternalLink className="h-3 w-3"/>
                                                </a>
                                            )}
                                        </div>
                                    </div>
                                    <button
                                        onClick={() => removeTask(task.id)}
                                        className="text-muted-foreground hover:text-red-400 transition-colors shrink-0 p-2 sm:p-1 self-end sm:self-auto"
                                    >
                                        <X className="h-5 w-5 sm:h-4 sm:w-4" />
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* Your uploads */}
                {me && myUploads.length > 0 && (
                    <div className="mt-8 rounded-2xl bg-[#131620] border border-white/5 p-6 sm:p-8 shadow-xl">
                        <h4 className="text-xs font-semibold text-muted-foreground tracking-widest uppercase mb-6">Your uploads</h4>
                        <ul className="divide-y divide-white/5">
                            {myUploads.map(u => (
                                <li key={u.id} className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-4 py-3">
                                    <span className="flex-1 min-w-0 truncate text-sm">{u.filename}</span>
                                    <span className={cn("text-xs font-medium", STATUS_CLASS[u.status])}>{STATUS_LABEL[u.status]}</span>
                                    {u.note && <span className="text-xs text-muted-foreground sm:max-w-xs">{u.note}</span>}
                                    {u.status === "live" && (
                                        <a href={paperUrl(u.id)} target="_blank" rel="noreferrer" className="text-xs text-cyan-400 hover:underline">Open</a>
                                    )}
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
            </div>
        </div>
    );
}
