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
import { useState } from "react";
import { BookOpen, Plus, Trash2, GripVertical } from "lucide-react";

type QuestionPart = {
    label: string;
    marks: number;
    instruction?: string;
    text?: string;
};

type Question = {
    id: string; // DnD needs unique ID
    number: number;
    bloomLevel: string;
    totalMarks: number;
    parts: QuestionPart[];
    rawGeneration?: string;
};

type Section = {
    id: string;
    name: string;
    instruction: string;
    bloomMode: "simple" | "advanced";
    difficulty?: string;
    bloomDistribution?: Record<string, number>;
    questions: Question[];
    pool?: Question[]; // Make pool optional but array
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
    const [generatedPaper, setGeneratedPaper] = useState<any>(null);
    const [isGenerating, setIsGenerating] = useState(false);
    const [activeId, setActiveId] = useState<string | null>(null);

    const sensors = useSensors(
        useSensor(PointerSensor),
        useSensor(KeyboardSensor, {
            coordinateGetter: sortableKeyboardCoordinates,
        })
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

        // Smart defaults based on section difficulty
        let defaultBloom = "understand";
        let defaultMarks = 6;

        if (section.bloomMode === "simple" && section.difficulty) {
            const diffMap = {
                "easy": { bloom: "remember", marks: 4 },
                "medium": { bloom: "apply", marks: 6 },
                "hard": { bloom: "analyze", marks: 10 }
            };
            const defaults = diffMap[section.difficulty as keyof typeof diffMap];
            if (defaults) {
                defaultBloom = defaults.bloom;
                defaultMarks = defaults.marks;
            }
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
            // Enable pool generation for all sections
            const sectionsWithPool = sections.map(s => ({ ...s, generate_pool: true }));

            const response = await fetch("http://localhost:8000/api/v1/generate/mock-paper", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ subject, sections: sectionsWithPool })
            });

            if (response.ok) {
                const data = await response.json();

                // Post-process to add IDs and ensure structure for DnD
                const processedSections = data.sections.map((s: any, idx: number) => ({
                    ...s,
                    id: sections[idx]?.id || `section-${idx}`,
                    questions: s.questions.map((q: any, qIdx: number) => ({ ...q, id: `sq-${idx}-${qIdx}` })),
                    pool: s.pool?.map((q: any, pIdx: number) => ({ ...q, id: `pq-${idx}-${pIdx}` })) || []
                }));

                // Debugging: Log the received data
                console.log("RAG Response Data:", data);
                console.log("Processed Sections:", processedSections);

                setGeneratedPaper({ ...data, sections: processedSections });

                // Also scroll to result
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

    // Drag and Drop Logic
    const handleDragStart = (event: any) => {
        setActiveId(event.active.id);
    };

    const handleDragEnd = (event: DragEndEvent) => {
        const { active, over } = event;
        setActiveId(null);

        if (!over) return;
        if (active.id === over.id) return;
        if (!generatedPaper) return;

        // Clone current state
        const newSections = [...generatedPaper.sections];

        // Find which section and which list (questions or pool) the items belong to
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

        // If in same section
        if (src.sIdx === dest.sIdx) {
            const section = newSections[src.sIdx];

            // 1. Reordering within same list
            if (src.list === dest.list) {
                if (src.list === 'questions') {
                    section.questions = arrayMove(section.questions, src.idx, dest.idx);
                    // Update numbers
                    section.questions.forEach((q: Question, i: number) => q.number = i + 1);
                } else {
                    section.pool = arrayMove(section.pool!, src.idx, dest.idx);
                }
            }
            // 2. Moving between lists (Swap/Move)
            else {
                // If moving from Pool to Questions (Swap if dropped on Question, Insert if different logic)
                const srcList = src.list === 'questions' ? section.questions : section.pool!;
                const destList = dest.list === 'questions' ? section.questions : section.pool!;

                const srcItem = srcList[src.idx];
                const destItem = destList[dest.idx];

                srcList[src.idx] = destItem;
                destList[dest.idx] = srcItem;

                // Update re-assigned numbers for main list
                section.questions.forEach((q: Question, i: number) => q.number = i + 1);
            }

            setGeneratedPaper({ ...generatedPaper, sections: newSections });
        }
    };

    // Helper to find the active question object for the drag overlay
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
                <div className="grid md:grid-cols-2 gap-4">
                    <div>
                        <label className="block text-sm font-medium mb-2">Subject</label>
                        <input
                            type="text"
                            value={subject}
                            onChange={(e) => setSubject(e.target.value)}
                            className="w-full px-3 py-2 rounded-md border border-input bg-background"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium mb-2">Duration (minutes)</label>
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
                </div>

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
                                    <div key={q.id || qIdx} className="flex items-center gap-2 p-2 bg-muted/30 rounded">
                                        <GripVertical className="h-4 w-4 text-muted-foreground" />
                                        <span className="text-sm">Q{q.number}</span>
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
                                                        {q.validation && q.validation.issues && q.validation.issues.length > 0 && (
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
