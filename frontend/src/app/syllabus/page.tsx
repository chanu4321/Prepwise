"use client";

import { useState, useRef } from "react";
import { Upload, X, FileText, CheckCircle, Loader2, AlertCircle, BookOpen } from "lucide-react";
import { cn } from "@/lib/utils";

export default function SyllabusUploadPage() {
    const [isDragging, setIsDragging] = useState(false);
    const [file, setFile] = useState<File | null>(null);
    const [subjectCode, setSubjectCode] = useState("");
    const [subjectName, setSubjectName] = useState("");
    const [isUploading, setIsUploading] = useState(false);
    const [uploadStatus, setUploadStatus] = useState<"idle" | "success" | "error">("idle");
    const [statusMessage, setStatusMessage] = useState("");
    const [extractedModules, setExtractedModules] = useState<any[] | null>(null);

    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    };

    const handleDragLeave = () => {
        setIsDragging(false);
    };

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        const droppedFiles = Array.from(e.dataTransfer.files).filter(f => f.type === "application/pdf");
        if (droppedFiles.length > 0) {
            setFile(droppedFiles[0]); // Only one syllabus at a time
            setUploadStatus("idle");
        }
    };

    const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files.length > 0) {
            const selectedFiles = Array.from(e.target.files).filter(f => f.type === "application/pdf");
            setFile(selectedFiles[0]);
            setUploadStatus("idle");
        }
    };

    const removeFile = () => {
        setFile(null);
        setExtractedModules(null);
        setUploadStatus("idle");
    };

    const handleUpload = async () => {
        if (!file || !subjectCode || !subjectName) {
            setUploadStatus("error");
            setStatusMessage("Please provide a file, subject code, and subject name.");
            return;
        }

        setIsUploading(true);
        setUploadStatus("idle");
        setExtractedModules(null);

        try {
            const formData = new FormData();
            formData.append("file", file);
            formData.append("subject_code", subjectCode);
            formData.append("subject_name", subjectName);

            const response = await fetch("http://localhost:8000/api/v1/syllabus/upload", {
                method: "POST",
                body: formData,
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || "Upload failed");
            }

            const data = await response.json();
            console.log("Syllabus upload success:", data);

            setUploadStatus("success");
            setStatusMessage("Syllabus processed successfully!");
            setExtractedModules(data.data.modules);
            
        } catch (error: any) {
            console.error(error);
            setUploadStatus("error");
            setStatusMessage(error.message || "Failed to process syllabus.");
        } finally {
            setIsUploading(false);
        }
    };

    return (
        <div className="container mx-auto flex min-h-[calc(100vh-58px)] flex-col items-center py-8">
            <div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-[500px] md:w-[600px]">
                <div className="flex flex-col space-y-2 text-center">
                    <BookOpen className="mx-auto h-8 w-8 text-primary" />
                    <h1 className="text-2xl font-semibold tracking-tight">Upload Subject Syllabus</h1>
                    <p className="text-sm text-muted-foreground">
                        Upload a syllabus PDF to automatically extract module-wise weightage for paper generation.
                    </p>
                </div>

                {/* Form Fields */}
                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1">
                        <label className="text-sm font-medium">Subject Code *</label>
                        <input 
                            type="text" 
                            className="w-full px-3 py-2 border rounded-md text-sm bg-background"
                            placeholder="e.g. CS101"
                            value={subjectCode}
                            onChange={(e) => setSubjectCode(e.target.value)}
                        />
                    </div>
                    <div className="space-y-1">
                        <label className="text-sm font-medium">Subject Name *</label>
                        <input 
                            type="text" 
                            className="w-full px-3 py-2 border rounded-md text-sm bg-background"
                            placeholder="e.g. Data Structures"
                            value={subjectName}
                            onChange={(e) => setSubjectName(e.target.value)}
                        />
                    </div>
                </div>

                <div
                    className={cn(
                        "relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-10 transition-colors",
                        isDragging ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:bg-muted/50",
                        !file ? "cursor-pointer" : ""
                    )}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                    onClick={() => !file && fileInputRef.current?.click()}
                >
                    <input
                        type="file"
                        ref={fileInputRef}
                        className="hidden"
                        accept="application/pdf"
                        onChange={handleFileSelect}
                    />

                    {!file ? (
                        <>
                            <Upload className="mb-4 h-10 w-10 text-muted-foreground" />
                            <p className="mb-2 text-sm font-medium">
                                Drag & drop your PDF or <span className="text-primary hover:underline">browse</span>
                            </p>
                            <p className="text-xs text-muted-foreground">
                                Only PDF files are supported
                            </p>
                        </>
                    ) : (
                        <div className="w-full">
                            <div className="flex items-center justify-between rounded-md border bg-background p-3">
                                <div className="flex items-center space-x-3 truncate">
                                    <FileText className="h-5 w-5 flex-shrink-0 text-primary" />
                                    <span className="truncate text-sm font-medium">{file.name}</span>
                                </div>
                                <button
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        removeFile();
                                    }}
                                    className="rounded-full p-1 hover:bg-muted"
                                >
                                    <X className="h-4 w-4 text-muted-foreground" />
                                </button>
                            </div>
                        </div>
                    )}
                </div>

                {uploadStatus === "error" && (
                    <div className="flex items-center gap-2 rounded-md bg-destructive/15 p-3 text-sm text-destructive">
                        <AlertCircle className="h-4 w-4 flex-shrink-0" />
                        <p>{statusMessage}</p>
                    </div>
                )}

                {uploadStatus === "success" && (
                    <div className="flex items-center gap-2 rounded-md bg-green-500/15 p-3 text-sm text-green-600 dark:text-green-400">
                        <CheckCircle className="h-4 w-4 flex-shrink-0" />
                        <p>{statusMessage}</p>
                    </div>
                )}

                <button
                    className="flex w-full items-center justify-center rounded-md bg-primary py-2.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50"
                    onClick={handleUpload}
                    disabled={isUploading || !file || !subjectCode || !subjectName}
                >
                    {isUploading ? (
                        <>
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            Processing Syllabus with OCR & AI...
                        </>
                    ) : (
                        "Upload and Extract Syllabus"
                    )}
                </button>
                
                {/* Extracted Modules Display */}
                {extractedModules && (
                    <div className="mt-8 border rounded-lg p-4 bg-muted/20">
                        <h3 className="font-medium flex items-center gap-2 mb-4">
                            <CheckCircle className="h-4 w-4 text-green-500" />
                            Successfully Extracted Modules
                        </h3>
                        
                        <div className="space-y-3">
                            {extractedModules.map((module, idx) => (
                                <div key={idx} className="flex flex-col sm:flex-row justify-between p-3 border rounded bg-background gap-4">
                                    <div className="flex-1">
                                        <h4 className="font-semibold text-sm">{module.name}</h4>
                                        <p className="text-xs text-muted-foreground line-clamp-2 mt-1" title={module.topics}>
                                            {module.topics}
                                        </p>
                                    </div>
                                    <div className="flex flex-col items-end justify-center shrink-0">
                                        <div className="text-xl font-bold text-primary">{module.weightage_percent}%</div>
                                        <div className="text-xs text-muted-foreground">Weightage</div>
                                    </div>
                                </div>
                            ))}
                        </div>
                        
                        <div className="mt-4 flex justify-between items-center text-sm pt-4 border-t">
                            <span className="font-medium">Total:</span>
                            <span className="font-bold">{extractedModules.reduce((sum, m) => sum + m.weightage_percent, 0)}%</span>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
