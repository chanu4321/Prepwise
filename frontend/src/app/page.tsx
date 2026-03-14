"use client";

import Link from "next/link";
import { useState, useEffect, useRef } from "react";
import {
    FileText, Brain, Zap, Search, Upload, BookOpen,
    ArrowRight, CheckCircle, ChevronRight, Layers, Cpu
} from "lucide-react";

const FEATURES = [
    {
        icon: <FileText className="h-7 w-7" />,
        title: "Advanced OCR",
        desc: "Convert handwritten lecture notes into high-fidelity digital assets instantly using proprietary neural vision models.",
        border: "hover:border-blue-500/40",
        iconColor: "text-blue-400",
        bg: "bg-blue-500/5",
    },
    {
        icon: <Search className="h-7 w-7" />,
        title: "Semantic Search",
        desc: "Find contextually relevant information across years of study materials with an AI that understands meaning, not just keywords.",
        border: "hover:border-purple-500/40",
        iconColor: "text-purple-400",
        bg: "bg-purple-500/5",
    },
    {
        icon: <Zap className="h-7 w-7" />,
        title: "Mock Papers",
        desc: "Generate custom practice exams tuned specifically to your university curriculum and professor's past patterns.",
        border: "hover:border-cyan-500/40",
        iconColor: "text-cyan-400",
        bg: "bg-cyan-500/5",
    },
    {
        icon: <Layers className="h-7 w-7" />,
        title: "Syllabus Mapping",
        desc: "Upload your syllabus and auto-distribute question marks proportionally across modules using AI weightage analysis.",
        border: "hover:border-emerald-500/40",
        iconColor: "text-emerald-400",
        bg: "bg-emerald-500/5",
    },
    {
        icon: <Brain className="h-7 w-7" />,
        title: "Bloom's Taxonomy",
        desc: "Every question is calibrated to the correct cognitive level — from recall to creation — ensuring full spectrum exam prep.",
        border: "hover:border-orange-500/40",
        iconColor: "text-orange-400",
        bg: "bg-orange-500/5",
    },
    {
        icon: <Cpu className="h-7 w-7" />,
        title: "RAG Pipeline",
        desc: "Retrieval-augmented generation grounds every question in your university's real past papers for maximum relevance.",
        border: "hover:border-pink-500/40",
        iconColor: "text-pink-400",
        bg: "bg-pink-500/5",
    },
];

const STATS = [
    { value: "10K+", label: "Questions Generated" },
    { value: "500+", label: "Past Papers Indexed" },
    { value: "6", label: "Bloom Levels" },
    { value: "100%", label: "AI Powered" },
];

const VALUE_POINTS = [
    "Syllabus-aware question distribution",
    "Bloom's Taxonomy cognitive alignment",
    "Drag-and-drop paper builder",
    "Multi-part question support",
    "Real-time mark fulfillment tracker",
];

const MODULE_PREVIEW = [
    { name: "Module 1 — Planning", pct: 75, label: "15/20m", ok: false },
    { name: "Module 2 — Risk Mgmt", pct: 100, label: "20/20m", ok: true },
    { name: "Module 3 — Estimation", pct: 50, label: "10/20m", ok: false },
    { name: "Module 4 — Quality", pct: 90, label: "18/20m", ok: true },
];

export default function Home() {
    const [glowX, setGlowX] = useState(50);
    const [glowY, setGlowY] = useState(40);
    const heroRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const handleMouse = (e: MouseEvent) => {
            const el = heroRef.current;
            if (!el) return;
            const rect = el.getBoundingClientRect();
            setGlowX(((e.clientX - rect.left) / rect.width) * 100);
            setGlowY(((e.clientY - rect.top) / rect.height) * 100);
        };
        window.addEventListener("mousemove", handleMouse);
        return () => window.removeEventListener("mousemove", handleMouse);
    }, []);

    const glowStyle = {
        background: `radial-gradient(600px circle at ${glowX}% ${glowY}%, rgba(59,130,246,0.10), transparent 70%)`,
    } as React.CSSProperties;

    return (
        <div className="min-h-screen bg-background text-foreground overflow-x-hidden">

            {/* ─── HERO ─── */}
            <section ref={heroRef} className="relative flex flex-col items-center justify-center min-h-[90vh] px-4 text-center overflow-hidden">

                {/* Mouse-following glow */}
                <div className="pointer-events-none absolute inset-0 transition-all duration-300" style={glowStyle} />

                {/* Subtle dot-grid */}
                <div className="pointer-events-none absolute inset-0 opacity-20"
                    style={{
                        backgroundImage: "radial-gradient(circle, hsl(var(--border)) 1px, transparent 1px)",
                        backgroundSize: "40px 40px",
                    }}
                />

                {/* Badge */}
                <div className="relative mb-6 inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-4 py-1.5 text-xs font-semibold text-primary uppercase tracking-widest">
                    <span className="h-1.5 w-1.5 rounded-full bg-primary animate-pulse" />
                    The Future of Learning
                </div>

                {/* Headline */}
                <h1 className="relative max-w-4xl text-5xl font-extrabold leading-tight tracking-tight sm:text-6xl md:text-7xl lg:text-8xl">
                    <span className="block">AI-Powered</span>
                    <span className="block bg-gradient-to-r from-primary via-blue-400 to-cyan-400 bg-clip-text text-transparent">
                        Academic
                    </span>
                    <span className="block">Mastery</span>
                </h1>

                <p className="relative mt-6 max-w-xl text-base sm:text-lg text-muted-foreground leading-relaxed">
                    Elevate your study workflow with high-end computational intelligence.
                    Designed for the modern scholar who demands elite performance.
                </p>

                {/* CTAs */}
                <div className="relative mt-10 flex flex-wrap items-center justify-center gap-4">
                    <Link
                        href="/generate"
                        className="group inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground shadow-lg hover:bg-primary/90 transition-all duration-200 hover:-translate-y-0.5"
                    >
                        Start Your Journey
                        <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                    </Link>
                    <Link
                        href="/papers"
                        className="inline-flex items-center gap-2 rounded-lg border border-border bg-background/60 px-6 py-3 text-sm font-semibold backdrop-blur hover:bg-muted transition-all duration-200 hover:-translate-y-0.5"
                    >
                        Browse Papers
                    </Link>
                </div>

                {/* Stats */}
                <div className="relative mt-16 grid grid-cols-2 gap-8 sm:grid-cols-4 max-w-2xl w-full border-t border-border pt-10">
                    {STATS.map((s) => (
                        <div key={s.label} className="text-center">
                            <div className="text-2xl font-bold">{s.value}</div>
                            <div className="text-xs text-muted-foreground mt-1">{s.label}</div>
                        </div>
                    ))}
                </div>

                {/* Scroll cue */}
                <div className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center text-muted-foreground/40">
                    <div className="h-10 w-6 rounded-full border border-current flex items-start justify-center p-1">
                        <div className="h-2 w-1 rounded-full bg-current animate-bounce" />
                    </div>
                </div>
            </section>

            {/* ─── BROWSER PREVIEW ─── */}
            <section className="container mx-auto max-w-5xl px-4 pb-24">
                <div className="rounded-2xl border border-border bg-card overflow-hidden shadow-2xl">
                    {/* Browser chrome */}
                    <div className="flex items-center gap-2 border-b border-border bg-muted/50 px-4 py-3">
                        <span className="h-3 w-3 rounded-full bg-red-500/70" />
                        <span className="h-3 w-3 rounded-full bg-yellow-500/70" />
                        <span className="h-3 w-3 rounded-full bg-green-500/70" />
                        <div className="ml-4 flex-1 rounded-md bg-background/60 px-3 py-1 text-xs text-muted-foreground font-mono">
                            prepwise.app/generate
                        </div>
                    </div>
                    <div className="p-6 space-y-4 bg-background/60">
                        <div className="grid grid-cols-3 gap-3">
                            {["Subject Code", "Duration (mins)", "Total Paper Marks"].map((label) => (
                                <div key={label}>
                                    <div className="h-3 w-24 rounded bg-muted mb-2" />
                                    <div className="h-9 rounded-lg border border-border bg-muted/30" />
                                </div>
                            ))}
                        </div>
                        <div className="rounded-lg border border-primary/20 bg-primary/5 p-4">
                            <div className="flex items-center justify-between mb-3">
                                <div className="h-4 w-48 rounded bg-primary/30" />
                                <div className="h-7 w-28 rounded-md bg-primary/40" />
                            </div>
                            <div className="grid grid-cols-4 gap-3">
                                {[22, 55, 80, 100].map((pct, i) => (
                                    <div key={i} className="bg-background rounded border border-border p-2">
                                        <div className="h-3 w-14 rounded bg-muted mb-2" />
                                        <div className="flex justify-between mb-2">
                                            <div className="h-2 w-6 rounded bg-muted" />
                                            <div className={`h-2 w-10 rounded ${pct === 100 ? "bg-green-500/50" : "bg-primary/30"}`} />
                                        </div>
                                        <div className="h-1 rounded-full bg-muted overflow-hidden">
                                            <div className="h-full bg-primary rounded-full" style={{ width: `${pct}%` }} />
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                        <div className="h-9 w-full rounded-lg bg-primary/20 border border-primary/30" />
                    </div>
                </div>
            </section>

            {/* ─── FEATURES ─── */}
            <section className="container mx-auto max-w-6xl px-4 pb-24">
                <div className="text-center mb-14">
                    <div className="inline-flex items-center gap-2 rounded-full border border-border bg-muted px-3 py-1 text-xs font-semibold uppercase tracking-widest text-muted-foreground mb-4">
                        Core Ecosystem
                    </div>
                    <h2 className="text-3xl font-bold sm:text-4xl md:text-5xl">
                        Bespoke Academic Features
                    </h2>
                    <p className="mt-4 text-muted-foreground max-w-lg mx-auto">
                        Precision-engineered tools for the scholar who refuses to compromise.
                    </p>
                </div>

                <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
                    {FEATURES.map((f) => (
                        <div
                            key={f.title}
                            className={`group relative rounded-2xl border border-border ${f.bg} p-6 transition-all duration-300 ${f.border} hover:-translate-y-1 hover:shadow-lg`}
                        >
                            <div className={`mb-4 ${f.iconColor}`}>{f.icon}</div>
                            <h3 className="text-base font-semibold mb-2">{f.title}</h3>
                            <p className="text-sm text-muted-foreground leading-relaxed">{f.desc}</p>
                            <div className="mt-4 inline-flex items-center gap-1 text-xs font-medium text-primary opacity-0 group-hover:opacity-100 transition-opacity">
                                Learn more <ChevronRight className="h-3 w-3" />
                            </div>
                        </div>
                    ))}
                </div>
            </section>

            {/* ─── VALUE PROP SPLIT ─── */}
            <section className="container mx-auto max-w-6xl px-4 pb-24">
                <div className="grid md:grid-cols-2 gap-12 items-center">
                    <div>
                        <div className="inline-flex items-center gap-2 rounded-full border border-border bg-muted px-3 py-1 text-xs font-semibold uppercase tracking-widest text-muted-foreground mb-4">
                            Premium Experience
                        </div>
                        <h2 className="text-3xl font-bold sm:text-4xl leading-tight mb-6">
                            Elite Performance for the
                            <span className="block text-primary">High-End Scholar</span>
                        </h2>
                        <p className="text-muted-foreground mb-8 leading-relaxed">
                            PrepWise is not just a tool — it is a personal academic strategist.
                            We combine cutting-edge Large Language Models with a bespoke user
                            interface designed to minimize friction and maximize cognitive output.
                        </p>
                        <ul className="space-y-3">
                            {VALUE_POINTS.map((item) => (
                                <li key={item} className="flex items-center gap-3 text-sm">
                                    <CheckCircle className="h-4 w-4 text-green-500 flex-shrink-0" />
                                    <span>{item}</span>
                                </li>
                            ))}
                        </ul>
                        <div className="mt-10 flex gap-3 flex-wrap">
                            <Link
                                href="/generate"
                                className="group inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 transition-all"
                            >
                                Generate a Paper <ArrowRight className="h-4 w-4 group-hover:translate-x-1 transition-transform" />
                            </Link>
                            <Link
                                href="/syllabus"
                                className="inline-flex items-center gap-2 rounded-lg border border-border px-5 py-2.5 text-sm font-semibold hover:bg-muted transition-all"
                            >
                                Upload Syllabus
                            </Link>
                        </div>
                    </div>

                    {/* Syllabus tracker mockup */}
                    <div className="relative hidden md:block">
                        <div className="relative rounded-2xl border border-border bg-card p-6 space-y-3 shadow-xl">
                            <div className="flex items-center justify-between mb-2">
                                <span className="text-sm font-semibold text-primary">SPM — Syllabus Strategy</span>
                                <span className="text-xs bg-primary/10 text-primary px-2 py-1 rounded-md font-medium">Auto-Distribute</span>
                            </div>
                            {MODULE_PREVIEW.map((m) => (
                                <div key={m.name} className="flex items-center gap-3">
                                    <div className="text-xs text-muted-foreground w-40 truncate">{m.name}</div>
                                    <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                                        <div
                                            className={`h-full rounded-full ${m.ok ? "bg-green-500" : "bg-primary"}`}
                                            style={{ width: `${m.pct}%` }}
                                        />
                                    </div>
                                    <div className={`text-xs font-semibold w-14 text-right ${m.ok ? "text-green-500" : "text-yellow-500"}`}>
                                        {m.label}
                                    </div>
                                </div>
                            ))}
                            <div className="pt-3 border-t border-border">
                                <div className="flex justify-between text-xs text-muted-foreground">
                                    <span>Total Configured</span>
                                    <span className="text-foreground font-semibold">63 / 80 marks</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            {/* ─── CTA BANNER ─── */}
            <section className="container mx-auto max-w-4xl px-4 pb-24">
                <div className="relative rounded-2xl border border-primary/20 bg-primary/5 p-10 text-center overflow-hidden">
                    <h2 className="text-3xl font-bold sm:text-4xl mb-4">Ready to ace your exams?</h2>
                    <p className="text-muted-foreground mb-8 max-w-lg mx-auto">
                        Generate your first AI-powered mock paper in under a minute. No signup required.
                    </p>
                    <Link
                        href="/generate"
                        className="group inline-flex items-center gap-2 rounded-lg bg-primary px-8 py-3.5 text-sm font-semibold text-primary-foreground shadow-lg hover:bg-primary/90 transition-all hover:-translate-y-0.5"
                    >
                        Generate Your Paper Free
                        <ArrowRight className="h-4 w-4 group-hover:translate-x-1 transition-transform" />
                    </Link>
                </div>
            </section>

            {/* ─── FOOTER ─── */}
            <footer className="border-t border-border">
                <div className="container mx-auto max-w-6xl px-4 py-10">
                    <div className="grid grid-cols-2 gap-8 md:grid-cols-4">
                        <div className="col-span-2 md:col-span-1">
                            <Link href="/" className="flex items-center gap-2 font-bold text-lg mb-3">
                                <BookOpen className="h-5 w-5 text-primary" />
                                PrepWise
                            </Link>
                            <p className="text-xs text-muted-foreground leading-relaxed max-w-44">
                                Revolutionizing the way scholars study with AI-driven precision.
                            </p>
                        </div>
                        <div>
                            <h4 className="text-sm font-semibold mb-3">Product</h4>
                            <ul className="space-y-2 text-sm text-muted-foreground">
                                <li><Link href="/generate" className="hover:text-foreground transition-colors">Generate Paper</Link></li>
                                <li><Link href="/papers" className="hover:text-foreground transition-colors">Browse Papers</Link></li>
                                <li><Link href="/syllabus" className="hover:text-foreground transition-colors">Syllabus Manager</Link></li>
                                <li><Link href="/upload" className="hover:text-foreground transition-colors">Upload Papers</Link></li>
                            </ul>
                        </div>
                        <div>
                            <h4 className="text-sm font-semibold mb-3">Company</h4>
                            <ul className="space-y-2 text-sm text-muted-foreground">
                                <li>About Us</li>
                                <li>Privacy Policy</li>
                                <li>Contact</li>
                            </ul>
                        </div>
                        <div>
                            <h4 className="text-sm font-semibold mb-3">Quick Links</h4>
                            <ul className="space-y-2 text-sm text-muted-foreground">
                                <li><Link href="/generate" className="hover:text-foreground transition-colors">Try Generate</Link></li>
                                <li><Link href="/upload" className="hover:text-foreground transition-colors">Upload a paper</Link></li>
                                <li><Link href="/syllabus" className="hover:text-foreground transition-colors">Upload syllabus</Link></li>
                            </ul>
                        </div>
                    </div>
                    <div className="mt-10 border-t border-border pt-6 flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-muted-foreground">
                        <span>2026 PrepWise. All rights reserved.</span>
                        <span>Built with Next.js · FastAPI · Qdrant · Ollama</span>
                    </div>
                </div>
            </footer>
        </div>
    );
}