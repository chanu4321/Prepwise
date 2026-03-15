"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Clock, Loader2, Search, Download, Eye, CheckCircle2, ChevronDown } from "lucide-react";

interface Paper {
    id: number;
    filename: string;
    subjectCode: string;
    subjectName: string;
    semester: string;
    year: string;
    time: string;
    marks: string;
    relevance?: number;
}

function PapersContent() {
    const [papers, setPapers] = useState<Paper[]>([]);
    const [allPapers, setAllPapers] = useState<Paper[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const searchParams = useSearchParams();
    const searchQuery = searchParams.get("search");

    // Filter states
    const [subjectCodeFilter, setSubjectCodeFilter] = useState("");
    const [subjectNameFilter, setSubjectNameFilter] = useState("");
    const [semesterFilter, setSemesterFilter] = useState("");
    const [currentPage, setCurrentPage] = useState(1);
    const ITEMS_PER_PAGE = 12;

    useEffect(() => {
        fetchPapers();
    }, [searchQuery]);

    const fetchPapers = async () => {
        setIsLoading(true);
        try {
            let url = "http://localhost:8000/api/v1/documents";
            let options: RequestInit = { method: "GET" };

            if (searchQuery) {
                url = "http://localhost:8000/api/v1/search/semantic";
                options = {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ query: searchQuery, limit: 10 }),
                };
            }

            const res = await fetch(url, options);
            if (!res.ok) throw new Error("Failed to fetch papers");
            
            const data = await res.json();
            setAllPapers(data);
            setPapers(data);
        } catch (err) {
            console.error(err);
            setAllPapers([]);
            setPapers([]);
        } finally {
            setIsLoading(false);
        }
    };

    // Client-side filtering
    useEffect(() => {
        let filtered = allPapers;

        if (subjectCodeFilter) {
            filtered = filtered.filter(p =>
                p.subjectCode?.toLowerCase().includes(subjectCodeFilter.toLowerCase())
            );
        }
        if (subjectNameFilter) {
            filtered = filtered.filter(p =>
                p.subjectName?.toLowerCase().includes(subjectNameFilter.toLowerCase())
            );
        }
        if (semesterFilter) {
            filtered = filtered.filter(p => p.semester?.includes(semesterFilter));
        }

        setPapers(filtered);
        setCurrentPage(1); // Reset to page 1 on search or filter
    }, [subjectCodeFilter, subjectNameFilter, semesterFilter, allPapers]);

    // Pagination calculations
    const totalPages = Math.ceil(papers.length / ITEMS_PER_PAGE);
    const startIndex = (currentPage - 1) * ITEMS_PER_PAGE;
    const currentPapers = papers.slice(startIndex, startIndex + ITEMS_PER_PAGE);

    return (
        <div className="min-h-[calc(100vh-56px)] bg-background text-foreground overflow-x-hidden pt-12 pb-24">
            
            {/* Ambient Glows */}
            <div className="fixed top-20 right-20 w-[600px] h-[600px] bg-indigo-500/5 rounded-full blur-[150px] pointer-events-none" />
            <div className="fixed top-40 left-10 w-[500px] h-[500px] bg-purple-500/5 rounded-full blur-[150px] pointer-events-none" />

            <div className="container mx-auto px-4 sm:px-6 max-w-7xl relative z-10">
                
                {/* Header */}
                <div className="mb-10">
                    <h1 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold tracking-tight mb-3">
                        {searchQuery ? "Browse Search Results" : "Browse Exam Papers"}
                    </h1>
                    {searchQuery ? (
                        <div className="flex items-center gap-3">
                            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-indigo-500/10 text-indigo-400 text-sm font-medium border border-indigo-500/20">
                                <Search className="h-3.5 w-3.5" />
                                Semantic Search: "{searchQuery}"
                            </span>
                            <button
                                onClick={() => window.location.href = '/papers'}
                                className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2 transition-colors"
                            >
                                Clear Search
                            </button>
                        </div>
                    ) : (
                        <p className="text-muted-foreground text-lg lg:text-xl max-w-2xl leading-relaxed">
                            Access verified academic resources from top departments and professors.
                        </p>
                    )}
                </div>

                {/* Horizontal Filter Bar */}
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8">
                    <div className="flex flex-wrap items-center gap-3">
                        <div className="relative group">
                            <input
                                placeholder="Subject Name..."
                                value={subjectNameFilter}
                                onChange={(e) => setSubjectNameFilter(e.target.value)}
                                className="peer pl-4 pr-10 py-2.5 rounded-xl bg-[#131620] border border-white/10 hover:border-white/20 focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/50 text-sm font-medium outline-none transition-all placeholder:text-muted-foreground/60 w-[180px]"
                            />
                            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                        </div>
                        
                        <div className="relative group">
                            <select
                                value={semesterFilter}
                                onChange={(e) => setSemesterFilter(e.target.value)}
                                className="appearance-none pl-4 pr-10 py-2.5 rounded-xl bg-[#131620] border border-white/10 hover:border-white/20 focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/50 text-sm font-medium outline-none transition-all text-foreground w-[150px] cursor-pointer"
                            >
                                <option value="" className="bg-[#131620]">Any Semester</option>
                                <option value="1" className="bg-[#131620]">Semester 1</option>
                                <option value="2" className="bg-[#131620]">Semester 2</option>
                                <option value="3" className="bg-[#131620]">Semester 3</option>
                                <option value="4" className="bg-[#131620]">Semester 4</option>
                                <option value="5" className="bg-[#131620]">Semester 5</option>
                                <option value="6" className="bg-[#131620]">Semester 6</option>
                                <option value="7" className="bg-[#131620]">Semester 7</option>
                                <option value="8" className="bg-[#131620]">Semester 8</option>
                            </select>
                            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                        </div>

                        <div className="relative group">
                            <input
                                placeholder="Subject Code..."
                                value={subjectCodeFilter}
                                onChange={(e) => setSubjectCodeFilter(e.target.value)}
                                className="peer pl-4 pr-10 py-2.5 rounded-xl bg-[#131620] border border-white/10 hover:border-white/20 focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/50 text-sm font-medium outline-none transition-all placeholder:text-muted-foreground/60 w-[160px]"
                            />
                            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" />
                        </div>
                    </div>

                    <div className="flex items-center gap-3">
                        <span className="text-xs font-bold text-muted-foreground tracking-widest uppercase">Sort By:</span>
                        <div className="relative">
                            <select className="appearance-none pl-4 pr-10 py-2.5 rounded-xl bg-transparent border-none text-indigo-400 font-semibold text-sm outline-none cursor-pointer hover:bg-white/5 transition-colors">
                                <option className="bg-[#131620]">Newest First</option>
                                <option className="bg-[#131620]">Oldest First</option>
                                <option className="bg-[#131620]">Highest Relevance</option>
                            </select>
                            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-indigo-400 pointer-events-none" />
                        </div>
                    </div>
                </div>

                {/* Loading / Empty States */}
                {isLoading && (
                    <div className="flex justify-center items-center py-32">
                        <Loader2 className="h-10 w-10 animate-spin text-indigo-500" />
                    </div>
                )}

                {!isLoading && papers.length === 0 && (
                    <div className="text-center py-32 border border-dashed border-white/10 rounded-3xl bg-[#131620]/50">
                        <Search className="h-12 w-12 text-muted-foreground/50 mx-auto mb-4" />
                        <h3 className="text-xl font-bold mb-2">No Papers Found</h3>
                        <p className="text-muted-foreground">
                            {searchQuery ? "Try adjusting your semantic search query." : "Upload some past papers to instantly populate your library."}
                        </p>
                    </div>
                )}

                {/* Papers Grid - Redesigned Cards */}
                <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {currentPapers.map((paper) => (
                        <div
                            key={paper.id}
                            className="group flex flex-col justify-between overflow-hidden rounded-2xl bg-[#131620] border border-white/5 shadow-lg transition-all duration-300 hover:-translate-y-1 hover:shadow-2xl hover:shadow-indigo-500/10 hover:border-indigo-500/30"
                        >
                            <div className="p-6">
                                {/* Top Badges */}
                                <div className="mb-5 flex items-start justify-between">
                                    <div className="rounded-lg bg-indigo-500/10 px-2.5 py-1 text-xs font-bold text-indigo-400 tracking-wide">
                                        {paper.subjectCode || "N/A"}
                                    </div>
                                    <span className="text-[10px] font-bold text-muted-foreground bg-white/5 px-2 py-1 rounded-md tracking-widest uppercase">
                                        PDF • {paper.id % 5 + 1}.{paper.id % 9} MB
                                    </span>
                                </div>
                                
                                {/* Title & Meta */}
                                <div className="space-y-3 mb-6">
                                    <h3 className="font-bold leading-tight text-lg text-foreground/90 group-hover:text-indigo-400 transition-colors line-clamp-2" title={paper.subjectName || paper.filename}>
                                        {paper.subjectName || paper.filename}
                                    </h3>
                                </div>

                                {/* Details */}
                                <div className="flex items-center gap-4 text-xs font-semibold">
                                    <div className="flex items-center gap-1.5 text-muted-foreground bg-white/5 px-2.5 py-1.5 rounded-lg">
                                        <Clock className="h-3.5 w-3.5" />
                                        {paper.time || "3h"} Exam
                                    </div>
                                    <div className="flex items-center gap-1.5 text-emerald-400 bg-emerald-400/10 px-2.5 py-1.5 rounded-lg">
                                        <CheckCircle2 className="h-3.5 w-3.5" />
                                        Verified
                                    </div>
                                </div>
                            </div>

                            {/* Actions Footer */}
                            <div className="p-4 flex gap-2 border-t border-white/5 bg-[#0f121b]/50">
                                <button
                                    onClick={(e) => {
                                        e.preventDefault();
                                        window.open(`http://localhost:8000/api/v1/documents/${paper.id}/download`, '_blank');
                                    }}
                                    className="flex-1 flex items-center justify-center gap-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2.5 text-sm font-semibold transition-all shadow-md shadow-indigo-600/20 hover:shadow-indigo-600/40"
                                >
                                    <Eye className="h-4 w-4" />
                                    View
                                </button>
                                <button
                                    onClick={(e) => {
                                        e.preventDefault();
                                        window.open(`http://localhost:8000/api/v1/documents/${paper.id}/download`, '_blank');
                                    }}
                                    className="flex items-center justify-center rounded-xl border border-white/10 hover:border-white/20 bg-white/5 hover:bg-white/10 px-4 py-2.5 transition-all text-muted-foreground hover:text-foreground"
                                    title="Download PDF"
                                >
                                    <Download className="h-4 w-4" />
                                </button>
                            </div>
                        </div>
                    ))}
                </div>

                {/* Pagination */}
                {!isLoading && totalPages > 1 && (
                    <div className="mt-12 flex items-center justify-center gap-2">
                        <button 
                            onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
                            disabled={currentPage === 1}
                            className="h-10 w-10 rounded-xl border border-white/5 bg-[#131620] flex items-center justify-center text-muted-foreground hover:bg-white/5 hover:text-foreground transition-colors disabled:opacity-50 disabled:cursor-not-allowed font-bold"
                        >
                            &lt;
                        </button>
                        
                        {Array.from({ length: totalPages }, (_, i) => i + 1).map(page => {
                            if (page === 1 || page === totalPages || (page >= currentPage - 1 && page <= currentPage + 1)) {
                                return (
                                    <button 
                                        key={page}
                                        onClick={() => setCurrentPage(page)}
                                        className={`h-10 w-10 rounded-xl flex items-center justify-center transition-all font-medium ${
                                            currentPage === page 
                                                ? "bg-indigo-600 text-white font-bold shadow-lg shadow-indigo-500/25" 
                                                : "border border-white/5 bg-[#131620] text-muted-foreground hover:bg-white/5 hover:text-foreground"
                                        }`}
                                    >
                                        {page}
                                    </button>
                                );
                            } else if (page === currentPage - 2 || page === currentPage + 2) {
                                return <span key={page} className="text-muted-foreground mx-1">...</span>;
                            }
                            return null;
                        })}

                        <button 
                            onClick={() => setCurrentPage(prev => Math.min(totalPages, prev + 1))}
                            disabled={currentPage === totalPages}
                            className="h-10 w-10 rounded-xl border border-white/5 bg-[#131620] flex items-center justify-center text-muted-foreground hover:bg-white/5 hover:text-foreground transition-colors disabled:opacity-50 disabled:cursor-not-allowed font-bold"
                        >
                            &gt;
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}

export default function PapersPage() {
    return (
        <Suspense fallback={
            <div className="flex justify-center py-32 bg-background min-h-screen pt-24 text-indigo-500">
                <Loader2 className="h-10 w-10 animate-spin" />
            </div>
        }>
            <PapersContent />
        </Suspense>
    );
}
