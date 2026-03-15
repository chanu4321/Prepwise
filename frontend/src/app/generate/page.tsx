"use client";

import {
    DndContext,
    closestCenter,
    KeyboardSensor,
    PointerSensor,
    useSensor,
    useSensors,
    DragOverlay,
    DragEndEvent
} from '@dnd-kit/core';
import {
    arrayMove,
    SortableContext,
    sortableKeyboardCoordinates,
    verticalListSortingStrategy
} from '@dnd-kit/sortable';
import { QuestionItem } from '@/components/QuestionItem';
import { useState, useEffect } from "react";
import { BookOpen, Plus, Trash2, GripVertical, ListFilter, MoreVertical, Sparkles, Brain, Search, PenTool, GitMerge, Target, Wand2 } from "lucide-react";
import { API_BASE_URL } from "@/lib/utils";

const getBloomIcon = (level: string) => {
    switch (level.toLowerCase()) {
        case 'remember': return <Brain className="h-3.5 w-3.5" />;
        case 'understand': return <Search className="h-3.5 w-3.5" />;
        case 'apply': return <PenTool className="h-3.5 w-3.5" />;
        case 'analyze': return <GitMerge className="h-3.5 w-3.5" />;
        case 'evaluate': return <Target className="h-3.5 w-3.5" />;
        case 'create': return <Sparkles className="h-3.5 w-3.5" />;
        default: return <Brain className="h-3.5 w-3.5" />;
    }
};

type QuestionPart = {
    label: string;
    marks: number;
    instruction?: string;
    text?: string;
};

type Question = {
    module?: string;
    id: string;
    number: number;
    bloomLevel: string;
    totalMarks: number;
    parts: QuestionPart[];
    rawGeneration?: string;
    validation?: any;
    isPool?: boolean;
};

type Section = {
    id: string;
    name: string;
    instruction: string;
    bloomMode: "simple" | "advanced";
    difficulty?: string;
    bloomDistribution?: Record<string, number>;
    questions: Question[];
    pool?: Question[];
    generate_pool?: boolean;
};

const bloomLevels = ["remember", "understand", "apply", "analyze", "evaluate", "create"];
const difficulties = ["easy", "medium", "hard"];

export default function GeneratePage() {
    const [subject, setSubject] = useState("Software Project Management");
    const [subjectCode, setSubjectCode] = useState("");
    const [duration, setDuration] = useState(180);
    const [sections, setSections] = useState<Section[]>([
        {
            id: "section-1",
            name: "Section A",
            instruction: "Attempt any 4 out of 5 questions",
            bloomMode: "simple",
            difficulty: "easy",
            questions: [
                {
                    id: "q-1",
                    number: 1,
                    bloomLevel: "remember",
                    totalMarks: 6,
                    parts: [{ label: "", marks: 6 }]
                }
            ],
            pool: []
        }
    ]);
    const [totalPaperMarks, setTotalPaperMarks] = useState(100);
    const [generatedPaper, setGeneratedPaper] = useState<any>(null);
    const [isGenerating, setIsGenerating] = useState(false);
    const [activeId, setActiveId] = useState<string | null>(null);
    const [syllabus, setSyllabus] = useState<any>(null);
    const [isFetchingSyllabus, setIsFetchingSyllabus] = useState(false);

    const fetchSyllabus = async () => {
        if (!subjectCode.trim()) return;
        setIsFetchingSyllabus(true);
        try {
            const res = await fetch(`${API_BASE_URL}/api/v1/syllabus/${subjectCode.trim()}`);
            if (res.ok) {
                const data = await res.json();
                setSyllabus(data.data);
            } else {
                setSyllabus(null);
            }
        } catch {
            setSyllabus(null);
        } finally {
            setIsFetchingSyllabus(false);
        }
    };

    useEffect(() => {
        const timer = setTimeout(() => { fetchSyllabus(); }, 800);
        return () => clearTimeout(timer);
    }, [subjectCode]);

    const assignedMarks = sections.reduce((acc, section) => {
        section.questions.forEach(q => {
            if (q.module) {
                acc[q.module] = (acc[q.module] || 0) + q.totalMarks;
            }
        });
        return acc;
    }, {} as Record<string, number>);

    // Greedy auto-distribution: assign modules to questions to best match syllabus weightage
    const autoDistributeModules = () => {
        if (!syllabus || !syllabus.modules.length) return;

        // 1. Calculate target marks per module
        const remaining: Record<string, number> = {};
        syllabus.modules.forEach((m: any) => {
            remaining[m.name] = Math.round(totalPaperMarks * (m.weightage_percent / 100));
        });

        // 2. Greedily assign each question to the module that still needs the most marks
        const updated = sections.map(section => ({
            ...section,
            questions: section.questions.map(q => {
                // Find module with highest remaining need
                const bestModule = Object.entries(remaining)
                    .sort(([, a], [, b]) => b - a)[0]?.[0];
                if (bestModule) {
                    remaining[bestModule] = Math.max(0, remaining[bestModule] - q.totalMarks);
                    return { ...q, module: bestModule };
                }
                return q;
            })
        }));
        setSections(updated);
    };

    const sensors = useSensors(
        useSensor(PointerSensor),
        useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
    );

    const addSection = () => {
        setSections([...sections, {
            id: `section-${Date.now()}`,
            name: `Section ${String.fromCharCode(65 + sections.length)}`,
            instruction: "Attempt all questions",
            bloomMode: "simple",
            difficulty: "medium",
            questions: [],
            pool: []
        }]);
    };

    const removeSection = (index: number) => {
        setSections(sections.filter((_, i) => i !== index));
    };

    const addQuestion = (sectionIndex: number) => {
        const updated = [...sections];
        const section = updated[sectionIndex];
        const newNumber = section.questions.length + 1;
        let defaultBloom = "understand";
        let defaultMarks = 6;
        if (section.bloomMode === "simple" && section.difficulty) {
            const distributions: Record<string, string[]> = {
                easy: ['remember', 'remember', 'understand', 'understand', 'apply'],
                medium: ['remember', 'understand', 'apply', 'apply', 'analyze'],
                hard: ['understand', 'apply', 'analyze', 'analyze', 'evaluate', 'create']
            };
            const dist = distributions[section.difficulty];
            if (dist) defaultBloom = dist[(newNumber - 1) % dist.length];
            const marksMap: Record<string, number> = { easy: 4, medium: 6, hard: 10 };
            defaultMarks = marksMap[section.difficulty] || 6;
        }
        updated[sectionIndex].questions.push({
            id: `q-${Date.now()}`,
            number: newNumber,
            bloomLevel: defaultBloom,
            totalMarks: defaultMarks,
            parts: [{ label: "", marks: defaultMarks }]
        });
        setSections(updated);
    };

    const generatePaper = async () => {
        setIsGenerating(true);
        try {
            const sectionsWithPool = sections.map(s => ({ ...s, generate_pool: true }));
            const response = await fetch(`${API_BASE_URL}/api/v1/generate/mock-paper`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ subject, sections: sectionsWithPool })
            });
            if (response.ok) {
                const data = await response.json();
                const processedSections = data.sections.map((s: any, idx: number) => ({
                    ...s,
                    id: sections[idx]?.id || `section-${idx}`,
                    questions: s.questions.map((q: any, qIdx: number) => ({ ...q, id: `sq-${idx}-${qIdx}` })),
                    pool: s.pool?.map((q: any, pIdx: number) => ({ ...q, id: `pq-${idx}-${pIdx}` })) || []
                }));
                setGeneratedPaper({ ...data, sections: processedSections });
                setTimeout(() => {
                    document.getElementById('generated-result')?.scrollIntoView({ behavior: 'smooth' });
                }, 100);
            } else {
                alert("Failed to generate paper");
            }
        } catch (error) {
            console.error(error);
            alert("Error generating paper");
        } finally {
            setIsGenerating(false);
        }
    };

    const handleDragStart = (event: any) => { setActiveId(event.active.id); };

    const handleDragEnd = (event: DragEndEvent) => {
        const { active, over } = event;
        setActiveId(null);
        if (!over || active.id === over.id || !generatedPaper) return;
        
        // DEEP COPY to avoid mutating React state directly
        const newSections = generatedPaper.sections.map((s: any) => ({
            ...s,
            questions: [...s.questions],
            pool: s.pool ? [...s.pool] : []
        }));

        const findLocation = (id: string) => {
            for (let sIdx = 0; sIdx < newSections.length; sIdx++) {
                const section = newSections[sIdx];
                const qIdx = section.questions.findIndex((q: Question) => q.id === id);
                if (qIdx !== -1) return { sIdx, list: 'questions', idx: qIdx };
                const pIdx = section.pool?.findIndex((q: Question) => q.id === id);
                if (pIdx !== -1) return { sIdx, list: 'pool', idx: pIdx };
            }
            return null;
        };
        const src = findLocation(active.id as string);
        const dest = findLocation(over.id as string);
        if (!src || !dest) return;
        
        const srcSection = newSections[src.sIdx];
        const destSection = newSections[dest.sIdx];

        if (src.sIdx === dest.sIdx && src.list === dest.list) {
            // Reordering within the same list
            if (src.list === 'questions') {
                srcSection.questions = arrayMove(srcSection.questions, src.idx, dest.idx);
                srcSection.questions.forEach((q: Question, i: number) => { q.number = i + 1; });
            } else {
                srcSection.pool = arrayMove(srcSection.pool!, src.idx, dest.idx);
            }
        } else {
            // Moving between lists/sections
            const srcList = src.list === 'questions' ? srcSection.questions : srcSection.pool!;
            const destList = dest.list === 'questions' ? destSection.questions : destSection.pool!;
            
            const [movedItem] = srcList.splice(src.idx, 1);
            destList.splice(dest.idx, 0, movedItem);

            // Update question numbers for both sections
            if (src.list === 'questions') srcSection.questions.forEach((q: Question, i: number) => { q.number = i + 1; });
            if (dest.list === 'questions') destSection.questions.forEach((q: Question, i: number) => { q.number = i + 1; });
        }
        setGeneratedPaper({ ...generatedPaper, sections: newSections });
    };

    const findActiveQuestion = (id: string) => {
        if (!generatedPaper) return null;
        for (const section of generatedPaper.sections) {
            const q = section.questions.find((q: Question) => q.id === id);
            if (q) return { ...q, isPool: false };
            const p = section.pool?.find((q: Question) => q.id === id);
            if (p) return { ...p, isPool: true };
        }
        return null;
    };

    const draggedItem = activeId ? findActiveQuestion(activeId) : null;

    return (
        <div className="container mx-auto px-4 py-8 max-w-5xl text-[#E2E8F0]">
            <div className="mb-10">
                <h1 className="text-3xl font-extrabold tracking-tight text-white mb-2">Generate Mock Paper</h1>
                <p className="text-[#94A3B8] text-sm font-medium">Engineer high-fidelity assessments with automated Bloom's Taxonomy mapping.</p>
            </div>

            {/* Inputs Row */}
            <div className="grid md:grid-cols-4 gap-6 mb-10">
                <div>
                    <label className="block text-[11px] font-bold text-[#64748B] uppercase tracking-wider mb-2">Subject Name</label>
                    <input
                        type="text"
                        value={subject}
                        onChange={(e) => setSubject(e.target.value)}
                        placeholder="e.g. Data Structures"
                        className="w-full bg-[#131620] border border-white/5 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/50 transition-all text-white font-medium"
                    />
                </div>
                <div>
                    <label className="block text-[11px] font-bold text-[#64748B] uppercase tracking-wider mb-2">Subject Code</label>
                    <input
                        type="text"
                        value={subjectCode}
                        onChange={(e) => setSubjectCode(e.target.value)}
                        placeholder="e.g. CSE432"
                        className="w-full bg-[#131620] border border-white/5 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/50 transition-all text-white font-medium"
                    />
                    {isFetchingSyllabus && <span className="text-xs text-indigo-400 mt-1 block">Fetching syllabus...</span>}
                    {!isFetchingSyllabus && subjectCode && syllabus && <span className="text-xs text-emerald-400 mt-1 block">✓ Syllabus loaded</span>}
                    {!isFetchingSyllabus && subjectCode && !syllabus && <span className="text-xs text-[#64748B] mt-1 block">No syllabus found</span>}
                </div>
                <div>
                    <label className="block text-[11px] font-bold text-[#64748B] uppercase tracking-wider mb-2">Duration (mins)</label>
                    <input
                        type="number"
                        value={duration}
                        onChange={(e) => {
                            const val = parseInt(e.target.value);
                            setDuration(isNaN(val) ? 0 : val);
                        }}
                        className="w-full bg-[#131620] border border-white/5 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/50 transition-all text-white font-medium"
                    />
                </div>
                <div>
                    <label className="block text-[11px] font-bold text-[#64748B] uppercase tracking-wider mb-2">Total Paper Marks</label>
                    <input
                        type="number"
                        value={totalPaperMarks}
                        onChange={(e) => {
                            const val = parseInt(e.target.value);
                            setTotalPaperMarks(isNaN(val) || val <= 0 ? 100 : val);
                        }}
                        className="w-full bg-[#131620] border border-white/5 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/50 transition-all text-white font-medium"
                    />
                </div>
            </div>

            {syllabus && (
                <div className="bg-[#131620] border border-indigo-500/20 rounded-xl p-4 mb-8">
                    <div className="flex items-center justify-between mb-4">
                        <h3 className="font-semibold text-indigo-400 text-sm">{syllabus.subject_name} ({syllabus.subject_code}) — Syllabus Strategy</h3>
                        <div className="flex items-center gap-3">
                            <button
                                onClick={autoDistributeModules}
                                className="text-xs bg-indigo-500/20 text-indigo-400 hover:bg-indigo-500/30 px-3 py-1.5 rounded-md font-medium transition-colors flex items-center gap-1"
                            >
                                ⚡ Auto-Distribute
                            </button>
                            <span className="text-sm font-medium text-white">
                                {sections.reduce((sum, s) => sum + s.questions.reduce((qSum, q) => qSum + q.totalMarks, 0), 0)} / {totalPaperMarks} marks
                            </span>
                        </div>
                    </div>

                    {/* Per-module progress bars */}
                    <div className="grid grid-cols-3 gap-x-6 gap-y-3">
                        {[...syllabus.modules].sort((a: any, b: any) => {
                            const na = parseInt((a.name.match(/\d+/) || ['0'])[0]);
                            const nb = parseInt((b.name.match(/\d+/) || ['0'])[0]);
                            return na - nb;
                        }).map((m: any) => {
                            const target = Math.round(totalPaperMarks * (m.weightage_percent / 100));
                            const assigned = assignedMarks[m.name] || 0;
                            const pct = target > 0 ? Math.min(100, Math.round((assigned / target) * 100)) : 0;
                            const isOver = assigned > target;
                            return (
                                <div key={m.name}>
                                    <div className="flex justify-between text-[11px] mb-1">
                                        <span className="text-[#94A3B8] font-medium truncate max-w-[60%]">{m.name}</span>
                                        <span className={isOver ? "text-orange-400 font-semibold" : assigned === target ? "text-emerald-400 font-semibold" : "text-[#64748B]"}>
                                            {assigned} / {target} marks
                                        </span>
                                    </div>
                                    <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                                        <div
                                            className={`h-full rounded-full transition-all duration-500 ${isOver ? 'bg-orange-400' : 'bg-indigo-500'}`}
                                            style={{ width: `${pct}%` }}
                                        />
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Sections Header */}
            <div className="flex items-center gap-2 mb-6">
                <ListFilter className="h-5 w-5 text-teal-400" />
                <h2 className="text-xl font-bold text-white">Paper Sections</h2>
            </div>

            {/* Sections List */}
            <div className="space-y-6 mb-10">
                {sections.map((section, sIdx) => (
                    <div key={section.id} className="bg-[#131620] border border-white/5 rounded-2xl overflow-hidden shadow-lg relative">
                        {/* Section Header (optional name input, or hidden depending on design. Mockup doesn't show a huge header but we need a way to delete) */}
                        <div className="flex justify-between items-center px-6 py-4 border-b border-white/5 bg-[#0f121b]">
                            <input
                                type="text"
                                value={section.name}
                                onChange={(e) => {
                                    const updated = [...sections];
                                    updated[sIdx].name = e.target.value;
                                    setSections(updated);
                                }}
                                className="text-sm font-bold bg-transparent border-0 focus:outline-none text-white tracking-wide"
                            />
                            <button onClick={() => removeSection(sIdx)} className="text-[#64748B] hover:text-red-400 transition-colors bg-[#1E2335] rounded p-1.5">
                                <Trash2 className="h-3.5 w-3.5" />
                            </button>
                        </div>

                        <div className="p-6">
                            {/* General Instruction */}
                            <div className="mb-6">
                                <label className="block text-[10px] font-bold text-[#64748B] uppercase tracking-widest mb-2">General Instruction</label>
                                <input
                                    type="text"
                                    value={section.instruction}
                                    onChange={(e) => {
                                        const updated = [...sections];
                                        updated[sIdx].instruction = e.target.value;
                                        setSections(updated);
                                    }}
                                    className="w-full bg-[#0A0D14] border border-white/5 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-indigo-500/50 transition-all text-[#CBD5E1]"
                                />
                            </div>

                            {/* Bloom Mode & Difficulty */}
                            <div className="grid md:grid-cols-2 gap-6 mb-8">
                                <div>
                                    <label className="block text-[10px] font-bold text-[#64748B] uppercase tracking-widest mb-2">Bloom Mode</label>
                                    <div className="flex bg-[#0A0D14] border border-white/5 p-1 rounded-xl">
                                        <button
                                            onClick={() => {
                                                const updated = [...sections];
                                                updated[sIdx].bloomMode = "simple";
                                                setSections(updated);
                                            }}
                                            className={`flex-1 px-4 py-2.5 rounded-lg text-xs font-semibold transition-all ${section.bloomMode === "simple" ? "bg-[#8050f2] text-white shadow-md shadow-indigo-500/20" : "text-[#64748B] hover:text-white"}`}
                                        >
                                            Simple / Fuzzy
                                        </button>
                                        <button
                                            onClick={() => {
                                                const updated = [...sections];
                                                updated[sIdx].bloomMode = "advanced";
                                                setSections(updated);
                                            }}
                                            className={`flex-1 px-4 py-2.5 rounded-lg text-xs font-semibold transition-all ${section.bloomMode === "advanced" ? "bg-[#8050f2] text-white shadow-md shadow-indigo-500/20" : "text-[#64748B] hover:text-white"}`}
                                        >
                                            Advanced / Manual
                                        </button>
                                    </div>
                                </div>
                                {section.bloomMode === "simple" && (
                                    <div>
                                        <label className="block text-[10px] font-bold text-[#64748B] uppercase tracking-widest mb-2">Difficulty Curve</label>
                                        <select
                                            value={section.difficulty}
                                            onChange={(e) => {
                                                const updated = [...sections];
                                                updated[sIdx].difficulty = e.target.value;
                                                setSections(updated);
                                            }}
                                            className="w-full appearance-none bg-[#0A0D14] border border-white/5 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-indigo-500/50 transition-all text-[#CBD5E1] cursor-pointer"
                                        >
                                            {difficulties.map(d => (
                                                <option key={d} value={d} className="bg-[#0A0D14]">{d === 'easy' ? 'Foundational (Easy)' : d === 'medium' ? 'Moderate (Balanced)' : 'Advanced (Hard)'}</option>
                                            ))}
                                        </select>
                                    </div>
                                )}
                            </div>

                            {/* Questions Table */}
                            <div>
                                <div className="grid grid-cols-12 gap-4 pb-3 border-b border-white/5 text-[10px] font-bold text-[#64748B] uppercase tracking-widest mb-4">
                                    <div className="col-span-2">Q#</div>
                                    <div className="col-span-4">Bloom ({section.bloomMode === 'simple' ? 'Auto' : 'Manual'})</div>
                                    {syllabus && <div className="col-span-3">Module</div>}
                                    <div className={syllabus ? "col-span-3" : "col-span-6"}>Marks</div>
                                </div>
                                
                                <div className="space-y-4">
                                    {section.questions.map((q, qIdx) => (
                                        <div key={q.id || qIdx} className="space-y-1">
                                            {/* Question Row */}
                                            <div className="grid grid-cols-12 gap-4 items-center group">
                                                {/* Q Number */}
                                                <div className="col-span-2 font-semibold text-teal-400 text-sm pl-2">
                                                    Q{q.number}
                                                </div>

                                                {/* Bloom */}
                                                <div className="col-span-4">
                                                    {section.bloomMode === "advanced" ? (
                                                        <select
                                                            value={q.bloomLevel}
                                                            onChange={(e) => {
                                                                const updated = [...sections];
                                                                updated[sIdx].questions[qIdx].bloomLevel = e.target.value;
                                                                setSections(updated);
                                                            }}
                                                            className="appearance-none bg-transparent border-none text-[#CBD5E1] text-sm focus:outline-none cursor-pointer w-full italic"
                                                        >
                                                            {bloomLevels.map(level => (
                                                                <option key={level} value={level} className="bg-[#131620] not-italic">{level.charAt(0).toUpperCase() + level.slice(1)}</option>
                                                            ))}
                                                        </select>
                                                    ) : (
                                                        <div className="flex items-center gap-2 text-[#CBD5E1] text-sm italic">
                                                            <span className="text-[#64748B]">{getBloomIcon(q.bloomLevel)}</span>
                                                            {q.bloomLevel.charAt(0).toUpperCase() + q.bloomLevel.slice(1)}ing
                                                        </div>
                                                    )}
                                                </div>

                                                {/* Module selector — only shown when syllabus loaded */}
                                                {syllabus && (
                                                    <div className="col-span-3">
                                                        <select
                                                            value={q.module || ""}
                                                            onChange={(e) => {
                                                                const updated = [...sections];
                                                                updated[sIdx].questions[qIdx].module = e.target.value || undefined;
                                                                setSections(updated);
                                                            }}
                                                            className="w-full appearance-none bg-[#0A0D14] border border-white/5 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:border-indigo-500/50 transition-all text-[#CBD5E1] cursor-pointer"
                                                        >
                                                            <option value="" className="bg-[#0A0D14]">— none —</option>
                                                            {syllabus.modules.map((m: any) => (
                                                                <option key={m.name} value={m.name} className="bg-[#0A0D14]">{m.name}</option>
                                                            ))}
                                                        </select>
                                                    </div>
                                                )}

                                                {/* Marks + Delete grouped together */}
                                                <div className={`${syllabus ? 'col-span-3' : 'col-span-6'} flex items-center gap-3`}>
                                                    <div className="flex bg-[#0A0D14] border border-white/5 rounded-lg px-3 py-2 w-[72px]">
                                                        <input
                                                            type="number"
                                                            value={q.totalMarks}
                                                            onChange={(e) => {
                                                                const val = parseInt(e.target.value);
                                                                const updated = [...sections];
                                                                updated[sIdx].questions[qIdx].totalMarks = isNaN(val) ? 0 : val;
                                                                setSections(updated);
                                                            }}
                                                            className="w-full bg-transparent border-none text-center text-sm font-semibold text-white focus:outline-none p-0"
                                                        />
                                                    </div>
                                                    <button
                                                        onClick={() => {
                                                            const updated = [...sections];
                                                            updated[sIdx].questions.splice(qIdx, 1);
                                                            updated[sIdx].questions.forEach((q, i) => { q.number = i + 1; });
                                                            setSections(updated);
                                                        }}
                                                        className="text-[#64748B] hover:text-red-400 transition-colors"
                                                        title="Remove Question"
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </button>
                                                </div>
                                            </div>

                                            {/* Multi-Part Configuration */}
                                            {(q.parts && (q.parts.length > 1 || q.parts[0]?.label)) ? (
                                                <div className="pl-14 pr-4 mt-1 mb-2">
                                                    <div className="bg-[#0f121b] border border-white/5 rounded-xl p-4 space-y-2">
                                                        {q.parts.map((part, pIdx) => (
                                                            <div key={pIdx} className="flex items-center gap-4">
                                                                <span className="text-[#64748B] font-bold text-xs uppercase w-6">({part.label})</span>
                                                                <div className="flex bg-[#0A0D14] border border-white/5 rounded-lg px-3 py-1.5 w-[70px]">
                                                                    <input
                                                                        type="number"
                                                                        value={part.marks}
                                                                        onChange={(e) => {
                                                                            const val = parseInt(e.target.value) || 0;
                                                                            const updated = [...sections];
                                                                            updated[sIdx].questions[qIdx].parts[pIdx].marks = val;
                                                                            const totalMarks = updated[sIdx].questions[qIdx].parts.reduce((sum, p) => sum + p.marks, 0);
                                                                            updated[sIdx].questions[qIdx].totalMarks = totalMarks;
                                                                            setSections(updated);
                                                                        }}
                                                                        className="w-full bg-transparent border-none text-center text-xs font-semibold text-white focus:outline-none p-0"
                                                                    />
                                                                </div>
                                                                <span className="text-[#64748B] text-xs">marks</span>
                                                                {q.parts.length > 2 && (
                                                                    <button
                                                                        onClick={() => {
                                                                            const updated = [...sections];
                                                                            updated[sIdx].questions[qIdx].parts.splice(pIdx, 1);
                                                                            const totalMarks = updated[sIdx].questions[qIdx].parts.reduce((sum, p) => sum + p.marks, 0);
                                                                            updated[sIdx].questions[qIdx].totalMarks = totalMarks;
                                                                            setSections(updated);
                                                                        }}
                                                                        className="text-red-500/50 hover:text-red-400 ml-auto transition-colors"
                                                                    >
                                                                        <Trash2 className="h-3 w-3" />
                                                                    </button>
                                                                )}
                                                            </div>
                                                        ))}
                                                        <div className="pt-2 border-t border-white/5 flex items-center justify-between">
                                                            <button
                                                                onClick={() => {
                                                                    const updated = [...sections];
                                                                    const parts = updated[sIdx].questions[qIdx].parts;
                                                                    const nextLabel = String.fromCharCode(97 + parts.length);
                                                                    parts.push({ label: nextLabel, marks: 2 });
                                                                    const totalMarks = parts.reduce((sum, p) => sum + p.marks, 0);
                                                                    updated[sIdx].questions[qIdx].totalMarks = totalMarks;
                                                                    setSections(updated);
                                                                }}
                                                                className="text-xs font-semibold text-teal-400 hover:text-teal-300 transition-colors flex items-center gap-1"
                                                            >
                                                                <Plus className="h-3 w-3" /> Add sub-part
                                                            </button>
                                                            <button
                                                                onClick={() => {
                                                                    const updated = [...sections];
                                                                    const totalMarks = updated[sIdx].questions[qIdx].parts.reduce((sum, p) => sum + p.marks, 0);
                                                                    updated[sIdx].questions[qIdx].parts = [{ label: '', marks: totalMarks }];
                                                                    setSections(updated);
                                                                }}
                                                                className="text-[10px] text-[#64748B] hover:text-white transition-colors"
                                                            >
                                                                Merge parts
                                                            </button>
                                                        </div>
                                                    </div>
                                                </div>
                                            ) : (
                                                <div className="pl-14 mb-2">
                                                    <button
                                                        onClick={() => {
                                                            const updated = [...sections];
                                                            const question = updated[sIdx].questions[qIdx];
                                                            const half = Math.floor(question.totalMarks / 2);
                                                            question.parts = [
                                                                { label: 'a', marks: half },
                                                                { label: 'b', marks: question.totalMarks - half }
                                                            ];
                                                            setSections(updated);
                                                        }}
                                                        className="text-[10px] font-bold text-[#64748B] hover:text-teal-400 transition-colors uppercase tracking-widest flex items-center gap-1"
                                                    >
                                                        <Plus className="h-3 w-3" /> Split into parts
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>

                                {/* Add Question Button */}
                                <div className="mt-6">
                                    <button
                                        onClick={() => addQuestion(sIdx)}
                                        className="text-sm font-semibold text-teal-400 hover:text-teal-300 transition-colors flex items-center gap-2"
                                    >
                                        <Plus className="h-4 w-4 bg-teal-400/20 rounded-full" />
                                        Add Question
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                ))}

                {/* Add New Section (Dashed) */}
                <button
                    onClick={addSection}
                    className="w-full border-2 border-dashed border-white/10 hover:border-white/20 bg-transparent rounded-2xl p-6 text-[#64748B] hover:text-[#94A3B8] font-bold text-xs uppercase tracking-widest transition-all flex flex-col items-center justify-center gap-3 mt-8"
                >
                    <div className="bg-[#1E2335] p-1 rounded text-white">
                        <Plus className="h-5 w-5" />
                    </div>
                    ADD NEW SECTION
                </button>
            </div>

            <div className="h-px bg-white/5 w-full my-8"></div>

            {/* Bottom Footer Actions */}
            <div className="flex sm:flex-row flex-col items-center justify-between gap-6 mb-12">
                <div className="flex flex-col gap-2 w-full sm:w-64">
                    <div className="flex justify-between text-xs font-semibold text-[#64748B]">
                        <span>Estimated Quality Score</span>
                        <span className="text-teal-400 font-bold">85%</span>
                    </div>
                    <div className="w-full bg-[#131620] h-2 rounded-full overflow-hidden">
                        <div className="bg-teal-400 h-full w-[85%] rounded-full shadow-[0_0_10px_rgba(45,212,191,0.5)]"></div>
                    </div>
                </div>

                <div className="flex items-center gap-4 w-full sm:w-auto">
                    <button className="flex-1 sm:flex-none border border-white/10 hover:bg-white/5 text-white text-sm font-semibold px-6 py-3 rounded-xl transition-colors">
                        Save as Draft
                    </button>
                    <button
                        onClick={generatePaper}
                        disabled={isGenerating}
                        className="flex-1 sm:flex-none bg-[#2f35bd] hover:bg-[#3942d9] text-white text-sm font-semibold px-6 py-3 rounded-xl transition-all shadow-[0_0_20px_rgba(47,53,189,0.3)] flex items-center justify-center gap-2 disabled:opacity-50"
                    >
                        {isGenerating ? (
                            <>
                                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
                                Generating...
                            </>
                        ) : (
                            <>
                                <Wand2 className="h-4 w-4" />
                                Generate Paper
                            </>
                        )}
                    </button>
                </div>
            </div>

            {/* Generated Paper Preview with Draggable Qs */}
            {generatedPaper && (
                <div id="generated-result" className="bg-card rounded-xl border p-6 space-y-6">
                    <div>
                        <h2 className="text-2xl font-bold">Generated Mock Paper</h2>
                        <p className="text-sm text-muted-foreground">
                            Based on: {generatedPaper.sourcePapers?.join(", ")}
                        </p>
                    </div>

                    <DndContext
                        sensors={sensors}
                        collisionDetection={closestCenter}
                        onDragStart={handleDragStart}
                        onDragEnd={handleDragEnd}
                    >
                        {generatedPaper.sections?.map((section: any, idx: number) => (
                            <div key={section.id} className="space-y-4 border rounded-lg p-4 bg-background shadow-sm border-primary/20">
                                <div className="border-b pb-2">
                                    <h3 className="text-xl font-semibold">{section.name}</h3>
                                    <p className="text-sm text-muted-foreground">{section.instruction}</p>
                                </div>

                                <div className="grid grid-cols-1 gap-4">
                                    {/* Selected Questions */}
                                    <div className="space-y-3">
                                        <h4 className="font-medium text-sm text-primary uppercase tracking-wider">Selected Questions</h4>
                                        <SortableContext
                                            items={section.questions.map((q: any) => q.id)}
                                            strategy={verticalListSortingStrategy}
                                        >
                                            <div className="space-y-3">
                                                {section.questions.map((q: any, i: number) => (
                                                    <div key={q.id} className="relative">
                                                        <QuestionItem id={q.id} question={q} index={i} />
                                                        {q.validation?.issues?.length > 0 && (
                                                            <div className={`mt-1 px-2 py-1 text-xs rounded ${q.validation.severity === 'error'
                                                                ? 'bg-red-100 text-red-800 border border-red-300'
                                                                : 'bg-yellow-100 text-yellow-800 border border-yellow-300'
                                                                }`}>
                                                                <span className="font-semibold">
                                                                    {q.validation.severity === 'error' ? '⚠ Validation Error:' : '⚡ Warning:'}
                                                                </span> {q.validation.issues.join(', ')}
                                                            </div>
                                                        )}
                                                    </div>
                                                ))}
                                            </div>
                                        </SortableContext>
                                    </div>

                                    {/* Pool Questions */}
                                    <div className="space-y-3 mt-4 pt-4 border-t border-dashed">
                                        <h4 className="font-medium text-sm text-muted-foreground uppercase tracking-wider">Extra Question Pool</h4>
                                        {section.pool && section.pool.length > 0 ? (
                                            <SortableContext
                                                items={section.pool.map((q: any) => q.id)}
                                                strategy={verticalListSortingStrategy}
                                            >
                                                <div className="grid gap-3">
                                                    {section.pool.map((q: any, i: number) => (
                                                        <QuestionItem key={q.id} id={q.id} question={q} index={i} isPool />
                                                    ))}
                                                </div>
                                            </SortableContext>
                                        ) : (
                                            <p className="text-sm text-muted-foreground italic">No extra questions generated.</p>
                                        )}
                                    </div>
                                </div>
                            </div>
                        ))}
                        <DragOverlay>
                            {activeId && draggedItem ? (
                                <QuestionItem
                                    id={getDragOverlayId(activeId)}
                                    question={draggedItem}
                                    index={0}
                                    isPool={draggedItem.isPool}
                                />
                            ) : null}
                        </DragOverlay>
                    </DndContext>
                </div>
            )}
        </div>
    );
}

function getDragOverlayId(id: string) {
    return id + "-overlay";
}
