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
import { BookOpen, Plus, Trash2, GripVertical } from "lucide-react";

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
        if (!subject) return;
        setIsFetchingSyllabus(true);
        try {
            const res = await fetch(`http://localhost:8000/api/v1/syllabus/${subject}`);
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
    }, [subject]);

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
            const response = await fetch("http://localhost:8000/api/v1/generate/mock-paper", {
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
        const newSections = [...generatedPaper.sections];
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
        if (!src || !dest || src.sIdx !== dest.sIdx) return;
        const section = newSections[src.sIdx];
        if (src.list === dest.list) {
            if (src.list === 'questions') {
                section.questions = arrayMove(section.questions, src.idx, dest.idx);
                section.questions.forEach((q: Question, i: number) => { q.number = i + 1; });
            } else {
                section.pool = arrayMove(section.pool!, src.idx, dest.idx);
            }
        } else {
            const srcList = src.list === 'questions' ? section.questions : section.pool!;
            const destList = dest.list === 'questions' ? section.questions : section.pool!;
            const tmp = srcList[src.idx];
            srcList[src.idx] = destList[dest.idx];
            destList[dest.idx] = tmp;
            section.questions.forEach((q: Question, i: number) => { q.number = i + 1; });
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
        <div className="container mx-auto px-4 py-8 max-w-6xl">
            <div className="flex items-center gap-3 mb-8">
                <BookOpen className="h-8 w-8 text-primary" />
                <h1 className="text-3xl font-bold">Generate Mock Examination Paper</h1>
            </div>

            {/* Configuration Form */}
            <div className="bg-card rounded-xl border p-6 mb-6 space-y-6">

                {/* Subject & Duration */}
                <div className="grid md:grid-cols-3 gap-4">
                    <div>
                        <label className="block text-sm font-medium mb-2">Subject Code</label>
                        <input
                            type="text"
                            value={subject}
                            onChange={(e) => setSubject(e.target.value)}
                            placeholder="e.g. CS101"
                            className="w-full px-3 py-2 rounded-md border border-input bg-background"
                        />
                        {isFetchingSyllabus && <span className="text-xs text-muted-foreground ml-1">Fetching syllabus...</span>}
                    </div>
                    <div>
                        <label className="block text-sm font-medium mb-2">Duration (mins)</label>
                        <input
                            type="number"
                            value={duration}
                            onChange={(e) => {
                                const val = parseInt(e.target.value);
                                setDuration(isNaN(val) ? 0 : val);
                            }}
                            className="w-full px-3 py-2 rounded-md border border-input bg-background"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium mb-2">Total Paper Marks</label>
                        <input
                            type="number"
                            value={totalPaperMarks}
                            onChange={(e) => {
                                const val = parseInt(e.target.value);
                                setTotalPaperMarks(isNaN(val) || val <= 0 ? 100 : val);
                            }}
                            className="w-full px-3 py-2 rounded-md border border-input bg-background"
                        />
                    </div>
                </div>

                {/* Syllabus Fulfillment Tracker */}
                {syllabus && (
                    <div className="bg-primary/5 border border-primary/20 rounded-lg p-4">
                        <div className="flex items-center justify-between mb-3">
                            <h3 className="font-semibold text-primary">{syllabus.subject_name} ({syllabus.subject_code}) — Syllabus Strategy</h3>
                            <div className="flex items-center gap-3">
                                <button
                                    onClick={autoDistributeModules}
                                    className="text-xs bg-primary text-primary-foreground px-3 py-1.5 rounded-md hover:bg-primary/90 font-medium"
                                    title="Auto-assign modules to questions based on syllabus weightage"
                                >
                                    ⚡ Auto-Distribute
                                </button>
                                <span className="text-sm font-medium">
                                    {sections.reduce((sum, s) => sum + s.questions.reduce((qSum, q) => qSum + q.totalMarks, 0), 0)} / {totalPaperMarks} marks
                                </span>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                            {syllabus.modules.map((m: any, idx: number) => {
                                const targetMarks = Math.round(totalPaperMarks * (m.weightage_percent / 100));
                                const actualMarks = assignedMarks[m.name] || 0;
                                const diff = actualMarks - targetMarks;
                                return (
                                    <div key={idx} className="bg-background rounded border p-2 text-sm">
                                        <div className="font-medium truncate" title={m.name}>{m.name}</div>
                                        <div className="flex justify-between text-xs mt-1">
                                            <span className="text-muted-foreground">{m.weightage_percent}%</span>
                                            <span className={`font-semibold ${diff === 0 ? 'text-green-600' : Math.abs(diff) <= 5 ? 'text-yellow-600' : 'text-red-600'}`}>
                                                {actualMarks} / {targetMarks} m
                                            </span>
                                        </div>
                                        <div className="w-full bg-muted h-1 mt-2 rounded-full overflow-hidden">
                                            <div
                                                className={`h-full ${actualMarks >= targetMarks ? 'bg-green-500' : 'bg-primary'}`}
                                                style={{ width: `${Math.min(targetMarks > 0 ? (actualMarks / targetMarks) * 100 : 0, 100)}%` }}
                                            />
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* Sections */}
                <div className="space-y-4">
                    <h3 className="text-lg font-semibold">Sections</h3>

                    {sections.map((section, sIdx) => (
                        <div key={section.id} className="border rounded-lg p-4 space-y-4">
                            <div className="flex items-center justify-between">
                                <input
                                    type="text"
                                    value={section.name}
                                    onChange={(e) => {
                                        const updated = [...sections];
                                        updated[sIdx].name = e.target.value;
                                        setSections(updated);
                                    }}
                                    className="text-lg font-semibold bg-transparent border-0 focus:outline-none"
                                />
                                <button
                                    onClick={() => removeSection(sIdx)}
                                    className="text-destructive hover:bg-destructive/10 p-2 rounded"
                                >
                                    <Trash2 className="h-4 w-4" />
                                </button>
                            </div>

                            {/* Instruction */}
                            <div>
                                <label className="block text-sm font-medium mb-1">Instruction</label>
                                <input
                                    type="text"
                                    value={section.instruction}
                                    onChange={(e) => {
                                        const updated = [...sections];
                                        updated[sIdx].instruction = e.target.value;
                                        setSections(updated);
                                    }}
                                    className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
                                />
                            </div>

                            {/* Bloom Mode Toggle */}
                            <div>
                                <label className="block text-sm font-medium mb-2">Bloom Mode</label>
                                <div className="flex gap-2">
                                    <button
                                        onClick={() => {
                                            const updated = [...sections];
                                            updated[sIdx].bloomMode = "simple";
                                            setSections(updated);
                                        }}
                                        className={`px-4 py-2 rounded-md text-sm ${section.bloomMode === "simple" ? "bg-primary text-primary-foreground" : "bg-secondary"}`}
                                    >
                                        Simple (Fuzzy)
                                    </button>
                                    <button
                                        onClick={() => {
                                            const updated = [...sections];
                                            updated[sIdx].bloomMode = "advanced";
                                            setSections(updated);
                                        }}
                                        className={`px-4 py-2 rounded-md text-sm ${section.bloomMode === "advanced" ? "bg-primary text-primary-foreground" : "bg-secondary"}`}
                                    >
                                        Advanced (Manual)
                                    </button>
                                </div>
                            </div>

                            {/* Simple Mode - Difficulty */}
                            {section.bloomMode === "simple" && (
                                <div>
                                    <label className="block text-sm font-medium mb-2">Difficulty</label>
                                    <select
                                        value={section.difficulty}
                                        onChange={(e) => {
                                            const updated = [...sections];
                                            updated[sIdx].difficulty = e.target.value;
                                            setSections(updated);
                                        }}
                                        className="w-full px-3 py-2 rounded-md border border-input bg-background"
                                    >
                                        {difficulties.map(d => (
                                            <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>
                                        ))}
                                    </select>
                                </div>
                            )}

                            {/* Questions */}
                            <div className="space-y-2">
                                <label className="block text-sm font-medium">Questions</label>

                                {section.questions.map((q, qIdx) => (
                                    <div key={q.id || qIdx}>
                                        <div className="flex items-center gap-2 p-2 bg-muted/30 rounded flex-wrap">
                                            <GripVertical className="h-4 w-4 text-muted-foreground" />
                                            <span className="text-sm">Q{q.number}</span>

                                            {section.bloomMode === "advanced" && (
                                                <select
                                                    value={q.bloomLevel}
                                                    onChange={(e) => {
                                                        const updated = [...sections];
                                                        updated[sIdx].questions[qIdx].bloomLevel = e.target.value;
                                                        setSections(updated);
                                                    }}
                                                    className="px-2 py-1 rounded border text-sm"
                                                >
                                                    {bloomLevels.map(level => (
                                                        <option key={level} value={level}>{level.charAt(0).toUpperCase() + level.slice(1)}</option>
                                                    ))}
                                                </select>
                                            )}

                                            {section.bloomMode === "simple" && (
                                                <span className="px-2 py-1 text-xs bg-primary/10 text-primary rounded">
                                                    {q.bloomLevel.charAt(0).toUpperCase() + q.bloomLevel.slice(1)}
                                                </span>
                                            )}

                                            {syllabus && (
                                                <select
                                                    value={q.module || ""}
                                                    onChange={(e) => {
                                                        const updated = [...sections];
                                                        updated[sIdx].questions[qIdx].module = e.target.value;
                                                        setSections(updated);
                                                    }}
                                                    className="px-2 py-1 rounded border text-sm max-w-[140px]"
                                                >
                                                    <option value="">Any Module</option>
                                                    {syllabus.modules.map((m: any, mIdx: number) => (
                                                        <option key={mIdx} value={m.name}>{m.name}</option>
                                                    ))}
                                                </select>
                                            )}

                                            <input
                                                type="number"
                                                value={q.totalMarks}
                                                onChange={(e) => {
                                                    const val = parseInt(e.target.value);
                                                    const updated = [...sections];
                                                    updated[sIdx].questions[qIdx].totalMarks = isNaN(val) ? 0 : val;
                                                    setSections(updated);
                                                }}
                                                className="w-16 px-2 py-1 rounded border text-sm"
                                            />
                                            <span className="text-sm text-muted-foreground">marks</span>

                                            <button
                                                onClick={() => {
                                                    const updated = [...sections];
                                                    const question = updated[sIdx].questions[qIdx];
                                                    if (question.parts.length === 1 && !question.parts[0].label) {
                                                        const half = Math.floor(question.totalMarks / 2);
                                                        question.parts = [
                                                            { label: "a", marks: half },
                                                            { label: "b", marks: question.totalMarks - half }
                                                        ];
                                                    } else {
                                                        question.parts = [{ label: "", marks: question.totalMarks }];
                                                    }
                                                    setSections(updated);
                                                }}
                                                className="text-xs text-muted-foreground hover:text-foreground"
                                                title={q.parts.length > 1 || q.parts[0]?.label ? "Remove parts" : "Make multi-part"}
                                            >
                                                {q.parts.length > 1 || q.parts[0]?.label ? `${q.parts.length} parts` : "+ Parts"}
                                            </button>

                                            <button
                                                onClick={() => {
                                                    const updated = [...sections];
                                                    updated[sIdx].questions.splice(qIdx, 1);
                                                    updated[sIdx].questions.forEach((q, i) => { q.number = i + 1; });
                                                    setSections(updated);
                                                }}
                                                className="ml-auto text-destructive/60 hover:text-destructive text-xs"
                                            >
                                                ✕
                                            </button>
                                        </div>

                                        {/* Multi-Part Configuration */}
                                        {(q.parts.length > 1 || q.parts[0]?.label) && (
                                            <div className="ml-8 mt-1 space-y-1 border-l-2 border-primary/20 pl-3">
                                                {q.parts.map((part, pIdx) => (
                                                    <div key={pIdx} className="flex items-center gap-2 text-xs">
                                                        <span className="text-muted-foreground">({part.label})</span>
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
                                                            className="w-12 px-1 py-0.5 rounded border"
                                                        />
                                                        <span className="text-muted-foreground">marks</span>
                                                        {q.parts.length > 2 && (
                                                            <button
                                                                onClick={() => {
                                                                    const updated = [...sections];
                                                                    updated[sIdx].questions[qIdx].parts.splice(pIdx, 1);
                                                                    const totalMarks = updated[sIdx].questions[qIdx].parts.reduce((sum, p) => sum + p.marks, 0);
                                                                    updated[sIdx].questions[qIdx].totalMarks = totalMarks;
                                                                    setSections(updated);
                                                                }}
                                                                className="text-destructive/70 hover:text-destructive"
                                                            >
                                                                ×
                                                            </button>
                                                        )}
                                                    </div>
                                                ))}
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
                                                    className="text-xs text-primary hover:underline"
                                                >
                                                    + Add part
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                ))}

                                <button
                                    onClick={() => addQuestion(sIdx)}
                                    className="text-sm text-primary hover:underline flex items-center gap-1"
                                >
                                    <Plus className="h-4 w-4" />
                                    Add Question
                                </button>
                            </div>
                        </div>
                    ))}

                    <button
                        onClick={addSection}
                        className="w-full border-2 border-dashed border-muted rounded-lg p-4 text-muted-foreground hover:border-primary hover:text-primary transition-colors flex items-center justify-center gap-2"
                    >
                        <Plus className="h-5 w-5" />
                        Add Section
                    </button>
                </div>

                <button
                    onClick={generatePaper}
                    disabled={isGenerating}
                    className="w-full bg-primary text-primary-foreground hover:bg-primary/90 py-3 rounded-md font-medium disabled:opacity-50"
                >
                    {isGenerating ? "Generating..." : "Generate Paper →"}
                </button>
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
