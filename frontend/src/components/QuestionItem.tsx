
import React from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical } from 'lucide-react';

interface QuestionItemProps {
    id: string;
    question: any;
    index: number;
    isPool?: boolean;
}

export function QuestionItem({ id, question, index, isPool }: QuestionItemProps) {
    const {
        attributes,
        listeners,
        setNodeRef,
        transform,
        transition,
        isDragging
    } = useSortable({ id });

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
    };

    return (
        <div
            ref={setNodeRef}
            style={style}
            className={`border rounded-lg p-4 bg-background relative group ${isPool ? 'border-dashed border-muted-foreground/40 bg-muted/10' : 'border-border'
                }`}
        >
            <div className="flex justify-between items-start gap-4">
                <div
                    {...attributes}
                    {...listeners}
                    className="mt-1 cursor-grab active:cursor-grabbing text-muted-foreground hover:text-foreground"
                >
                    <GripVertical className="h-5 w-5" />
                </div>

                <div className="flex-1">
                    <p className={`font-medium ${isPool ? 'text-muted-foreground' : ''}`}>
                        {isPool ? '[Spare] ' : `Q${question.number || index + 1}. `}
                        <span className="text-muted-foreground text-sm">
                            ({question.bloomLevel}, {question.totalMarks} marks)
                        </span>
                    </p>

                    <div className="mt-2 space-y-1">
                        {question.parts?.map((part: any, pIdx: number) => (
                            <p key={pIdx} className={`text-sm ${isPool ? 'text-muted-foreground/80' : ''}`}>
                                {part.label && `(${part.label}) `}{part.text}
                            </p>
                        ))}

                        {/* Fallback if no parts but raw text */}
                        {!question.parts?.length && question.rawGeneration && (
                            <p className="text-sm line-clamp-3">{question.rawGeneration}</p>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
