"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const LINKS = [
    { href: "/admin/papers", label: "Papers" },
    { href: "/admin/users", label: "Users" },
];

export function AdminNav() {
    const pathname = usePathname();
    return (
        <nav className="mb-6 flex gap-2">
            {LINKS.map(link => (
                <Link
                    key={link.href}
                    href={link.href}
                    className={cn(
                        "rounded-md px-3 py-1.5 text-sm transition-colors",
                        pathname === link.href ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground",
                    )}
                >
                    {link.label}
                </Link>
            ))}
        </nav>
    );
}
