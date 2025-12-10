"use client";

import { useState, useRef } from "react";
import { Upload, X, FileText, CheckCircle, Loader2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export default function UploadPage() {
    const [isDragging, setIsDragging] = useState(false);
    const [files, setFiles] = useState<File[]>([]);
    const [isUploading, setIsUploading] = useState(false);
    const [uploadStatus, setUploadStatus] = useState<"idle" | "success" | "error">("idle");
    const [statusMessage, setStatusMessage] = useState("");

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
            setFiles(prev => [...prev, ...droppedFiles]);
            setUploadStatus("idle");
        }
    };

    const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files.length > 0) {
            const selectedFiles = Array.from(e.target.files).filter(f => f.type === "application/pdf");
            setFiles(prev => [...prev, ...selectedFiles]);
            setUploadStatus("idle");
        }
    };

    const handleClick = () => {
        fileInputRef.current?.click();
    };

    const removeFile = (index: number) => {
        setFiles(prev => prev.filter((_, i) => i !== index));
    };

    const handleUpload = async () => {
        if (files.length === 0) return;

        setIsUploading(true);
        setUploadStatus("idle");

        try {
            // Upload files one by one (or could be Promise.all)
            for (const file of files) {
                const formData = new FormData();
                formData.append("file", file);

                const response = await fetch("http://localhost:8000/api/v1/documents/ingest", {
                    method: "POST",
                    body: formData,
                });

                if (!response.ok) {
                    throw new Error(`Failed to upload ${file.name}`);
                }

                const data = await response.json();
                console.log("Upload success:", data);
            }

            setUploadStatus("success");
            setStatusMessage("All files processed successfully!");
            setFiles([]); // Clear queue on success
        } catch (error) {
            console.error(error);
            setUploadStatus("error");
            setStatusMessage("Failed to process some files. Check console.");
        } finally {
            setIsUploading(false);
        }
    };

    return (
        <div className="container flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center py-10">
            <div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-[350px] md:w-[500px]">
                <div className="flex flex-col space-y-2 text-center">
                    <h1 className="text-2xl font-semibold tracking-tight">Upload Question Papers</h1>
                    <p className="text-sm text-muted-foreground">
                        Drag and drop your PDF files here to start the extraction process.
                    </p>
                </div>

                <div
                    className={cn(
                        "relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/25 px-6 py-10 transition-colors hover:bg-muted/50 cursor-pointer",
                        isDragging && "border-primary bg-muted/50",
                        isUploading && "opacity-50 pointer-events-none"
                    )}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                    onClick={handleClick}
                >
                    <input
                        type="file"
                        ref={fileInputRef}
                        className="hidden"
                        onChange={handleFileSelect}
                        multiple
                        accept=".pdf"
                    />
                    <div className="flex flex-col items-center justify-center space-y-4 text-center">
                        <div className="rounded-full bg-primary/10 p-4">
                            <Upload className="h-8 w-8 text-primary" />
                        </div>
                        <div className="space-y-1">
                            <p className="text-sm font-medium">
                                <span className="text-primary underline">Click to upload</span> or drag and drop
                            </p>
                            <p className="text-xs text-muted-foreground">PDF only (Max 10MB)</p>
                        </div>
                    </div>
                </div>

                {uploadStatus === "error" && (
                    <div className="flex items-center gap-2 p-3 text-sm text-red-500 bg-red-50 rounded-md border border-red-100">
                        <AlertCircle className="h-4 w-4" />
                        {statusMessage}
                    </div>
                )}

                {uploadStatus === "success" && (
                    <div className="flex items-center gap-2 p-3 text-sm text-green-600 bg-green-50 rounded-md border border-green-100">
                        <CheckCircle className="h-4 w-4" />
                        {statusMessage}
                    </div>
                )}

                {files.length > 0 && (
                    <div className="space-y-4">
                        <div className="text-sm font-medium">Files to Process ({files.length})</div>
                        <div className="divide-y rounded-md border max-h-[200px] overflow-y-auto">
                            {files.map((file, i) => (
                                <div key={i} className="flex items-center justify-between p-3">
                                    <div className="flex items-center space-x-3 overflow-hidden">
                                        <FileText className="h-5 w-5 text-muted-foreground flex-shrink-0" />
                                        <span className="text-sm truncate">{file.name}</span>
                                    </div>
                                    <button
                                        onClick={(e) => { e.stopPropagation(); removeFile(i); }}
                                        className="text-muted-foreground hover:text-red-500"
                                        disabled={isUploading}
                                    >
                                        <X className="h-4 w-4" />
                                    </button>
                                </div>
                            ))}
                        </div>
                        <button
                            onClick={handleUpload}
                            disabled={isUploading}
                            className="w-full flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-70 disabled:cursor-not-allowed"
                        >
                            {isUploading ? (
                                <>
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    Processing...
                                </>
                            ) : (
                                "Process Files"
                            )}
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
