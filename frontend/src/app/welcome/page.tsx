"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { GraduationCap, Presentation } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";
import { AccessNotice } from "@/components/AccessNotice";
import { apiFetch, describeApiError } from "@/lib/api";

const CHOICES = [
    {
        role: "student" as const,
        icon: GraduationCap,
        title: "Student",
        text: "Browse and search past papers, and upload up to 20 papers a day.",
    },
    {
        role: "faculty" as const,
        icon: Presentation,
        title: "Faculty (trial)",
        text: "Generate up to 3 mock papers a day. An admin can verify you for higher limits and uploads.",
    },
];

export default function WelcomePage() {
    const { ready, me, refresh } = useAuth();
    const router = useRouter();
    const [saving, setSaving] = useState<string | null>(null);
    const [error, setError] = useState("");

    if (!ready) return null;
    if (!me) return <AccessNotice title="Sign in first" message="Sign in with your Microsoft account to choose a role." showSignIn />;
    if (me.role) return <AccessNotice title="You're all set" message="Your role is already chosen. An admin can change it if needed." />;

    const choose = async (role: "student" | "faculty") => {
        setSaving(role);
        setError("");
        try {
            await apiFetch("/api/v1/me/role", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ role }),
            });
            await refresh();
            router.replace(role === "faculty" ? "/generate" : "/papers");
        } catch (e) {
            setError(describeApiError(e));
            setSaving(null);
        }
    };

    return (
        <div className="container mx-auto max-w-3xl px-4 py-20">
            <h1 className="mb-2 text-center text-3xl font-bold">Welcome to PrepWise</h1>
            <p className="mb-10 text-center text-muted-foreground">How will you use it? You can only choose once.</p>
            {error && <p className="mb-6 text-center text-sm text-red-400">{error}</p>}
            <div className="grid gap-6 sm:grid-cols-2">
                {CHOICES.map(({ role, icon: Icon, title, text }) => (
                    <button
                        key={role}
                        disabled={saving !== null}
                        onClick={() => choose(role)}
                        className="rounded-2xl border border-border bg-card p-8 text-left transition-all hover:-translate-y-1 hover:border-primary/50 disabled:opacity-50"
                    >
                        <Icon className="mb-4 h-8 w-8 text-primary" />
                        <h2 className="mb-2 text-lg font-semibold">{title}</h2>
                        <p className="text-sm text-muted-foreground">{text}</p>
                        {saving === role && <p className="mt-4 text-xs text-primary">Saving…</p>}
                    </button>
                ))}
            </div>
        </div>
    );
}
