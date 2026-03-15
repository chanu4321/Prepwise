"use client";

import { useState, useRef, useCallback } from "react";
import { CloudUpload, X, CheckCircle, Loader2, BookOpen, ChevronRight, FileText } from "lucide-react";
import { cn } from "@/lib/utils";

type ProcessState = "idle" | "uploading" | "success" | "error";

export default function SyllabusUploadPage() {
    const [isDragging, setIsDragging] = useState(false);
    const [subjectCode, setSubjectCode] = useState("");
    const [subjectName, setSubjectName] = useState("");
    const [file, setFile] = useState<File | null>(null);
    const [processState, setProcessState] = useState<ProcessState>("idle");
    const [progress, setProgress] = useState(0);
    const [errorMessage, setErrorMessage] = useState("");
    const [extractedModules, setExtractedModules] = useState<any[] | null>(null);

    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    };

    const handleDragLeave = () => {
        setIsDragging(false);
    };

    const processFile = useCallback((selectedFile: File) => {
        if (!subjectCode.trim() || !subjectName.trim()) {
            setErrorMessage("Please enter both Subject Code and Subject Name before uploading.");
            return;
        }

        setFile(selectedFile);
        setProcessState("uploading");
        setProgress(0);
        setErrorMessage("");
        setExtractedModules(null);

        // Fake progress interval
        let currentProgress = 0;
        const progressInterval = setInterval(() => {
            currentProgress += Math.floor(Math.random() * 10) + 2;
            if (currentProgress > 90) currentProgress = 90;
            setProgress(currentProgress);
        }, 400);

        // Actual API call
        const formData = new FormData();
        formData.append("file", selectedFile);
        formData.append("subject_code", subjectCode);
        formData.append("subject_name", subjectName);

        fetch("http://localhost:8000/api/v1/syllabus/upload", {
            method: "POST",
            body: formData,
        })
        .then(async (res) => {
            if (!res.ok) {
                const errorData = await res.json().catch(() => ({}));
                throw new Error(errorData.detail || "Upload failed");
            }
            return res.json();
        })
        .then((data) => {
            clearInterval(progressInterval);
            setProgress(100);
            setProcessState("success");
            setExtractedModules(data.data.modules);
        })
        .catch(err => {
            clearInterval(progressInterval);
            setProcessState("error");
            setErrorMessage(err.message || "Failed to upload syllabus");
        });
    }, [subjectCode, subjectName]);

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        const droppedFiles = Array.from(e.dataTransfer.files).filter(f => f.type === "application/pdf");
        if (droppedFiles.length > 0) {
            processFile(droppedFiles[0]);
        }
    };

    const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files.length > 0) {
            const selectedFiles = Array.from(e.target.files).filter(f => f.type === "application/pdf");
            if (selectedFiles.length > 0) processFile(selectedFiles[0]);
        }
    };

    const reset = () => {
        setFile(null);
        setProcessState("idle");
        setProgress(0);
        setExtractedModules(null);
        setErrorMessage("");
    };

    return (
        <div className="min-h-[calc(100vh-56px)] bg-background text-foreground overflow-x-hidden pt-16 pb-24 flex flex-col items-center">
            
            {/* Ambient Glows */}
            <div className="fixed top-20 right-20 w-[600px] h-[600px] bg-emerald-500/5 rounded-full blur-[150px] pointer-events-none" />
            <div className="fixed bottom-20 left-20 w-[500px] h-[500px] bg-cyan-500/5 rounded-full blur-[150px] pointer-events-none" />

            <div className="container mx-auto max-w-2xl px-4 relative z-10 w-full">
                
                {/* Header */}
                <div className="text-center mb-12">
                    <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight mb-4 flex items-center justify-center gap-3">
                        <BookOpen className="h-10 w-10 text-emerald-400" />
                        Upload <span className="bg-gradient-to-r from-emerald-400 to-cyan-500 bg-clip-text text-transparent">Syllabus</span>
                    </h1>
                    <p className="text-muted-foreground text-lg">
                        Feed AI your university curriculum to automatically map modules and track mark distributions.
                    </p>
                </div>

                {/* State Machine UI */}
                {processState === "idle" || processState === "error" ? (
                    <div className="bg-[#0f121b]/80 backdrop-blur-xl border border-white/10 rounded-3xl p-6 sm:p-10 shadow-2xl">
                        
                        {/* Error Banner */}
                        {errorMessage && (
                            <div className="mb-6 bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl text-sm flex items-start gap-3">
                                <span className="text-lg leading-none mt-0.5">⚠️</span>
                                <div>
                                    <p className="font-semibold">Action Required</p>
                                    <p className="opacity-80">{errorMessage}</p>
                                </div>
                            </div>
                        )}

                        <div className="grid grid-cols-2 gap-4 mb-8">
                            <div>
                                <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Subject Code</label>
                                <input 
                                    type="text" 
                                    placeholder="e.g. CS401"
                                    value={subjectCode}
                                    onChange={(e) => setSubjectCode(e.target.value)}
                                    className="w-full bg-black/40 border border-white/5 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500/50 focus:border-emerald-500/50 transition-all font-medium"
                                />
                            </div>
                            <div>
                                <label className="block text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Subject Name</label>
                                <input 
                                    type="text" 
                                    placeholder="e.g. Distributed Systems"
                                    value={subjectName}
                                    onChange={(e) => setSubjectName(e.target.value)}
                                    className="w-full bg-black/40 border border-white/5 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-500/50 focus:border-emerald-500/50 transition-all font-medium"
                                />
                            </div>
                        </div>

                        <div
                            className={cn(
                                "flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-16 transition-all duration-300 cursor-pointer group",
                                isDragging 
                                    ? "border-emerald-400 bg-emerald-500/10 scale-[1.02]" 
                                    : "border-white/10 hover:border-emerald-500/30 hover:bg-white/[0.02]"
                            )}
                            onDragOver={handleDragOver}
                            onDragLeave={handleDragLeave}
                            onDrop={handleDrop}
                            onClick={() => fileInputRef.current?.click()}
                        >
                            <input
                                type="file"
                                ref={fileInputRef}
                                className="hidden"
                                onChange={handleFileSelect}
                                accept=".pdf"
                            />
                            
                            <div className="h-16 w-16 rounded-2xl bg-gradient-to-br from-emerald-500/20 to-cyan-500/20 flex items-center justify-center mb-6 border border-emerald-500/20 group-hover:scale-110 transition-transform duration-300">
                                <CloudUpload className="h-8 w-8 text-emerald-400" />
                            </div>
                            
                            <h3 className="text-xl font-bold mb-2">Select Syllabus PDF</h3>
                            <p className="text-muted-foreground text-sm font-medium text-center">
                                Drag & drop or click to browse
                            </p>
                        </div>
                    </div>
                ) : (
                    /* Processing & Success State */
                    <div className="bg-[#0f121b]/80 backdrop-blur-xl border border-white/10 rounded-3xl p-8 sm:p-12 shadow-2xl text-center relative overflow-hidden flex flex-col items-center">
                        
                        {processState === "success" && (
                            <div className="absolute inset-0 bg-gradient-to-t from-emerald-500/10 to-transparent pointer-events-none" />
                        )}

                        <div className="relative z-10 w-full flex flex-col items-center">
                            <div className="mb-6 h-20 w-20 rounded-full bg-[#131620] shadow-inner flex items-center justify-center border border-white/5 relative">
                                {processState === "uploading" ? (
                                    <>
                                        <Loader2 className="h-10 w-10 text-emerald-400 animate-spin absolute" />
                                        <FileText className="h-4 w-4 text-emerald-400/50 animate-pulse" />
                                    </>
                                ) : (
                                    <CheckCircle className="h-10 w-10 text-emerald-400" />
                                )}
                            </div>

                            <h2 className="text-2xl font-bold mb-2">
                                {processState === "uploading" ? "Extracting Curriculum..." : "Syllabus Processed"}
                            </h2>
                            <p className="text-sm text-muted-foreground mb-8 text-center max-w-sm">
                                {processState === "uploading" 
                                    ? "Our vision models are reading the modules and extracting weightage automatically."
                                    : `Successfully mapped curriculum for ${subjectCode} — ${subjectName}.`}
                            </p>

                            {/* Progress Bar */}
                            {processState === "uploading" && (
                                <div className="w-full max-w-md">
                                    <div className="flex justify-between text-xs font-medium mb-3">
                                        <span className="text-emerald-400 animate-pulse">Running OCR & LLM Extraction</span>
                                        <span className="text-cyan-400 tabular-nums">{progress}%</span>
                                    </div>
                                    <div className="h-2 bg-black/40 rounded-full overflow-hidden border border-white/5">
                                        <div 
                                            className="h-full bg-gradient-to-r from-emerald-400 to-cyan-400 transition-all duration-300 ease-out relative"
                                            style={{ width: `${progress}%` }}
                                        >
                                            <div className="absolute inset-0 bg-white/20 w-full animate-[shimmer_2s_infinite]" style={{ transform: 'skewX(-20deg)' }} />
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* Finished - Show Results Inline */}
                            {processState === "success" && extractedModules && (
                                <div className="w-full mt-6 text-left animate-in fade-in slide-in-from-bottom-4 duration-700">
                                    <div className="flex items-center justify-between mb-4">
                                        <span className="text-xs font-bold text-muted-foreground tracking-widest uppercase">Extracted Modules</span>
                                        <span className="text-xs font-semibold bg-emerald-500/10 text-emerald-400 px-2 py-1 rounded-md">{extractedModules.length} Modules</span>
                                    </div>
                                    
                                    <div className="space-y-2 max-h-[400px] overflow-y-auto pr-2 custom-scrollbar">
                                        {[...extractedModules].sort((a, b) => {
                                            const na = parseInt((a.name.match(/\d+/) || ['0'])[0]);
                                            const nb = parseInt((b.name.match(/\d+/) || ['0'])[0]);
                                            return na - nb;
                                        }).map((m, i) => (
                                            <div key={i} className="bg-black/40 border border-white/5 rounded-xl p-4 hover:bg-black/60 transition-colors">
                                                <div className="flex items-start justify-between gap-3">
                                                    <div className="flex items-start gap-3 overflow-hidden">
                                                        <div className="h-8 w-8 rounded bg-white/5 flex items-center justify-center text-xs font-bold shrink-0 mt-0.5">{i+1}</div>
                                                        <div className="overflow-hidden">
                                                            <p className="text-sm font-semibold truncate">{m.name}</p>
                                                            {m.topics && (
                                                                <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{m.topics}</p>
                                                            )}
                                                        </div>
                                                    </div>
                                                    <div className="text-emerald-400 font-bold shrink-0 text-sm">{m.weightage_percent}%</div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>

                                    <div className="mt-8 flex gap-3 w-full">
                                        <button onClick={reset} className="flex-1 bg-white/5 border border-white/10 hover:bg-white/10 rounded-xl py-3 text-sm font-semibold transition-all">
                                            Upload Another
                                        </button>
                                        <a href="/generate" className="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl py-3 text-sm font-semibold transition-all shadow-lg flex items-center justify-center gap-2">
                                            Generate Paper <ChevronRight className="h-4 w-4" />
                                        </a>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
