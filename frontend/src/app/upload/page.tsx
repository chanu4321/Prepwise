"use client";

import { useState, useRef, useCallback } from "react";
import { API_BASE_URL } from "@/lib/utils";
import { CloudUpload, X, FileText, CheckCircle, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

type UploadTask = {
    id: string;
    file: File;
    progress: number;
    status: "uploading" | "success" | "error";
    errorMessage?: string;
};

export default function UploadPage() {
    const [isDragging, setIsDragging] = useState(false);
    const [tasks, setTasks] = useState<UploadTask[]>([]);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    };

    const handleDragLeave = () => {
        setIsDragging(false);
    };

    const processFiles = useCallback((selectedFiles: File[]) => {
        const newTasks = selectedFiles.map(file => ({
            id: Math.random().toString(36).substring(7),
            file,
            progress: 0,
            status: "uploading" as const
        }));

        setTasks(prev => [...prev, ...newTasks]);

        // Process each file
        newTasks.forEach(task => {
            // Fake progress interval
            let currentProgress = 0;
            const progressInterval = setInterval(() => {
                currentProgress += Math.floor(Math.random() * 15) + 5;
                if (currentProgress > 90) currentProgress = 90; // Cap at 90% until done
                
                setTasks(prev => prev.map(t => 
                    t.id === task.id && t.status === "uploading" 
                        ? { ...t, progress: currentProgress } 
                        : t
                ));
            }, 300);

            // Actual API call
            const formData = new FormData();
            formData.append("file", task.file);

            fetch(`${API_BASE_URL}/api/v1/documents/ingest`, {
                method: "POST",
                body: formData,
            })
            .then(res => {
                if (!res.ok) throw new Error("Upload failed");
                return res.json();
            })
            .then(() => {
                clearInterval(progressInterval);
                setTasks(prev => prev.map(t => 
                    t.id === task.id ? { ...t, progress: 100, status: "success" } : t
                ));
            })
            .catch(err => {
                clearInterval(progressInterval);
                setTasks(prev => prev.map(t => 
                    t.id === task.id ? { ...t, status: "error", errorMessage: "Failed to upload" } : t
                ));
            });
        });
    }, []);

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        const droppedFiles = Array.from(e.dataTransfer.files).filter(f => f.type === "application/pdf" || f.type.startsWith("image/"));
        if (droppedFiles.length > 0) processFiles(droppedFiles);
    };

    const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files.length > 0) {
            const selectedFiles = Array.from(e.target.files).filter(f => f.type === "application/pdf" || f.type.startsWith("image/"));
            processFiles(selectedFiles);
        }
    };

    const removeTask = (id: string) => {
        setTasks(prev => prev.filter(t => t.id !== id));
    };

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
                        accept=".pdf,image/png,image/jpeg"
                    />
                    
                    <div className="mb-6 h-20 w-20 rounded-full bg-[#171b29] flex items-center justify-center shadow-inner">
                        <CloudUpload className={cn("h-10 w-10 text-cyan-400 transition-transform duration-300", isDragging && "scale-110 -translate-y-1")} />
                    </div>
                    
                    <h3 className="text-2xl font-bold mb-3">Drag and drop your papers</h3>
                    <p className="text-muted-foreground text-sm mb-8 font-medium">
                        PDF, JPG, or PNG files supported (Max 20MB per file)
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
                                                <span className="text-xs font-medium text-muted-foreground tabular-nums">
                                                    {task.status === "uploading" && `${task.progress}%`}
                                                    {task.status === "success" && <span className="text-green-500 flex items-center gap-1"><CheckCircle className="h-3 w-3"/> Done</span>}
                                                    {task.status === "error" && <span className="text-red-500 flex items-center gap-1"><AlertCircle className="h-3 w-3"/> Failed</span>}
                                                </span>
                                            </div>
                                            {/* Progress bar container */}
                                            {task.status === "uploading" ? (
                                                <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden">
                                                    <div 
                                                        className="h-full bg-gradient-to-r from-cyan-400 to-emerald-400 rounded-full transition-all duration-300 ease-out" 
                                                        style={{ width: `${task.progress}%` }} 
                                                    />
                                                </div>
                                            ) : task.status === "success" ? (
                                                <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden">
                                                    <div className="h-full bg-green-500 rounded-full w-full" />
                                                </div>
                                            ) : (
                                                <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden">
                                                    <div className="h-full bg-red-500 rounded-full w-full" />
                                                </div>
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
            </div>
        </div>
    );
}
