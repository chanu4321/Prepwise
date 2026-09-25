"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth, type Role } from "@/components/AuthProvider";
import { AccessNotice } from "@/components/AccessNotice";
import { apiFetch, describeApiError } from "@/lib/api";

type AdminUser = {
    id: number;
    email: string | null;
    name: string | null;
    role: Role | null;
    verified: boolean;
    createdAt: string | null;
    usedToday: { generate: number; upload: number; syllabus: number };
};

export default function AdminUsersPage() {
    const { ready, me } = useAuth();
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [error, setError] = useState("");
    const isAdmin = me?.role === "admin";

    const load = useCallback(async () => {
        try {
            const response = await apiFetch("/api/v1/admin/users");
            setUsers(((await response.json()) as { users: AdminUser[] }).users);
            setError("");
        } catch (e) {
            setError(describeApiError(e));
        }
    }, []);

    useEffect(() => {
        if (!isAdmin) return;

        void (async () => {
            await load();
        })();
    }, [isAdmin, load]);

    const update = async (id: number, change: { role?: Role; verified?: boolean }) => {
        try {
            await apiFetch(`/api/v1/admin/users/${id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(change),
            });
            await load();
        } catch (e) {
            setError(describeApiError(e));
        }
    };

    if (!ready) return null;
    if (!isAdmin) return <AccessNotice title="Admins only" message="This page is for PrepWise admins." showSignIn={!me} />;

    return (
        <div className="container mx-auto max-w-6xl px-4 py-12">
            <h1 className="mb-6 text-3xl font-bold">Users</h1>
            {error && <p className="mb-4 text-sm text-red-400">{error}</p>}
            <div className="overflow-x-auto rounded-xl border border-border">
                <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
                        <tr>
                            <th className="p-3">User</th>
                            <th className="p-3">Role</th>
                            <th className="p-3">Verified</th>
                            <th className="p-3">Today (gen / up / syl)</th>
                            <th className="p-3">Joined</th>
                        </tr>
                    </thead>
                    <tbody>
                        {users.map((u) => (
                            <tr key={u.id} className="border-t border-border">
                                <td className="p-3">
                                    <div className="font-medium">{u.name ?? "-"}</div>
                                    <div className="text-xs text-muted-foreground">{u.email ?? "-"}</div>
                                </td>
                                <td className="p-3">
                                    <select
                                        value={u.role ?? ""}
                                        disabled={u.id === me?.id}
                                        onChange={(e) => void update(u.id, { role: e.target.value as Role })}
                                        className="rounded-md border border-border bg-background px-2 py-1"
                                    >
                                        {u.role === null && <option value="">(not chosen)</option>}
                                        <option value="student">Student</option>
                                        <option value="faculty">Faculty</option>
                                        <option value="admin">Admin</option>
                                    </select>
                                </td>
                                <td className="p-3">
                                    <input
                                        type="checkbox"
                                        checked={u.verified}
                                        disabled={u.role !== "faculty"}
                                        onChange={(e) => void update(u.id, { verified: e.target.checked })}
                                        aria-label={`Verified for ${u.email ?? u.id}`}
                                    />
                                </td>
                                <td className="p-3 tabular-nums">
                                    {u.usedToday.generate} / {u.usedToday.upload} / {u.usedToday.syllabus}
                                </td>
                                <td className="p-3 text-muted-foreground">
                                    {u.createdAt ? new Date(u.createdAt).toLocaleDateString() : "-"}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
