"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { FileText, Calendar, Clock, BarChart, Filter, Loader2 } from "lucide-react";

interface Paper {
    id: number;
    filename: string;
    subjectCode: string;
    subjectName: string;
    semester: string;
    year: string;
    time: string;
    marks: string;
}

export default function PapersPage() {
    const [papers, setPapers] = useState<Paper[]>([]);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        fetchPapers();
    }, []);

    const fetchPapers = async () => {
        try {
            const res = await fetch("http://localhost:8000/api/v1/documents");
            if (!res.ok) throw new Error("Failed to fetch papers");
            const data = await res.json();
            setPapers(data);
        } catch (err) {
            console.error(err);
            setPapers([]); // Show empty state instead of error
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="container mx-auto px-4 py-8 md:px-6">
            <div className="flex flex-col gap-4 pb-8 border-b mb-8">
                <h1 className="text-3xl font-bold tracking-tight text-primary">Browse Papers</h1>
                <p className="text-muted-foreground text-lg">
                    Explore our collection of university question papers with AI-powered insights.
                </p>
            </div>

            <div className="grid gap-8 md:grid-cols-4 lg:grid-cols-5 relative">
                {/* Sidebar Filters */}
                <div className="space-y-6 md:col-span-1">
                    <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-5 sticky top-20">
                        <div className="flex items-center gap-2 font-semibold mb-6 text-lg border-b pb-2">
                            <Filter className="h-5 w-5 text-primary" />
                            <span>Filters</span>
                        </div>

                        <div className="space-y-5">
                            <div className="space-y-2">
                                <label className="text-sm font-medium leading-none text-foreground/80">Subject Code</label>
                                <input className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2" placeholder="e.g. CS101" />
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium leading-none text-foreground/80">Subject Name</label>
                                <input className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2" placeholder="e.g. Operating Systems" />
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium leading-none text-foreground/80">Semester</label>
                                <select className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50">
                                    <option value="">Any Semester</option>
                                    <option value="1">1st Sem</option>
                                    <option value="2">2nd Sem</option>
                                    <option value="3">3rd Sem</option>
                                    <option value="4">4th Sem</option>
                                    <option value="5">5th Sem</option>
                                    <option value="6">6th Sem</option>
                                    <option value="7">7th Sem</option>
                                    <option value="8">8th Sem</option>
                                </select>
                            </div>

                            <div className="pt-4">
                                <button className="w-full inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-10 px-4 py-2 transition-all active:scale-[0.98]">
                                    Apply Filters
                                </button>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Results Grid */}
                <div className="md:col-span-3 lg:col-span-4">
                    {isLoading && (
                        <div className="flex justify-center py-20">
                            <Loader2 className="h-8 w-8 animate-spin text-primary" />
                        </div>
                    )}

                    {!isLoading && papers.length === 0 && (
                        <div className="text-center py-20 text-muted-foreground">
                            No papers found. Upload some to get started!
                        </div>
                    )}

                    <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
                        {papers.map((paper) => (
                            <div
                                key={paper.id}
                                className="group relative flex flex-col justify-between overflow-hidden rounded-xl border bg-card text-card-foreground shadow-sm transition-all duration-300 hover:shadow-xl hover:-translate-y-1 hover:border-primary/50"
                            >
                                <div className="p-6">
                                    <div className="mb-4 flex items-start justify-between">
                                        <div className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-semibold text-primary border border-blue-100">
                                            {paper.subjectCode || "N/A"}
                                        </div>
                                        <span className="text-xs font-medium text-muted-foreground bg-muted px-2 py-0.5 rounded">PDF</span>
                                    </div>
                                    <div className="mb-4 space-y-1">
                                        <h3 className="font-bold leading-tight tracking-tight text-lg group-hover:text-primary transition-colors line-clamp-2">
                                            {paper.subjectName || paper.filename}
                                        </h3>
                                        <p className="text-xs text-muted-foreground truncate" title={paper.filename}>
                                            {paper.filename}
                                        </p>
                                    </div>

                                    <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground border-t pt-4">
                                        <div className="flex items-center gap-1.5">
                                            <span className="font-semibold text-foreground">{paper.semester || "?"}</span>
                                        </div>
                                        <div className="flex items-center gap-1.5">
                                            <Calendar className="h-3.5 w-3.5" />
                                            {paper.year || "N/A"}
                                        </div>
                                        <div className="flex items-center gap-1.5">
                                            <Clock className="h-3.5 w-3.5" />
                                            {paper.time || "-"}
                                        </div>
                                    </div>
                                </div>

                                <div className="p-4 pt-0">
                                    <button
                                        onClick={(e) => {
                                            e.preventDefault();
                                            window.open(`http://localhost:8000/api/v1/documents/${paper.id}/download`, '_blank');
                                        }}
                                        className="w-full flex items-center justify-center gap-2 rounded-lg border border-input bg-background px-4 py-2.5 text-sm font-medium shadow-sm transition-all hover:bg-primary hover:text-primary-foreground hover:border-primary group-hover:shadow-md"
                                    >
                                        <FileText className="h-4 w-4" />
                                        View Paper
                                    </button>
                                    <span className="sr-only">View {paper.filename}</span>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
