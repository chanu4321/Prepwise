"use client";

import Link from "next/link";
import { BookOpen, Upload, Shield } from "lucide-react";
import { AuthMenu } from "@/components/AuthMenu";
import { useAuth } from "@/components/AuthProvider";

export function Navbar() {
    const { me } = useAuth();
    const canUploadSyllabus = me?.role === "admin" || (me?.role === "faculty" && me.verified);

    return (
        <nav className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 sticky top-0 z-50">
            <div className="container mx-auto flex h-14 items-center px-4 md:px-6">
                <Link href="/" className="mr-6 flex items-center space-x-2">
                    <BookOpen className="h-6 w-6 text-primary" />
                    <span className="hidden font-bold sm:inline-block">PrepWise</span>
                </Link>
                <div className="flex flex-1 items-center justify-between space-x-2 md:justify-end">
                    <div className="flex items-center space-x-4 md:space-x-6 text-sm font-medium">
                        {canUploadSyllabus && (
                            <Link href="/syllabus" className="flex items-center space-x-2 text-foreground/60 transition-colors hover:text-foreground/80">
                                <BookOpen className="h-4 w-4" />
                                <span>Syllabus</span>
                            </Link>
                        )}
                        <Link href="/upload" className="flex items-center space-x-2 text-foreground/60 transition-colors hover:text-foreground/80">
                            <Upload className="h-4 w-4" />
                            <span>Upload Papers</span>
                        </Link>
                        <Link href="/papers" className="text-foreground/60 transition-colors hover:text-foreground/80">
                            Browse Papers
                        </Link>
                        <Link href="/generate" className="text-primary font-semibold transition-colors hover:text-primary/80">
                            Generate Paper
                        </Link>
                        {me?.role === "admin" && (
                            <Link href="/admin/users" className="flex items-center space-x-1 text-foreground/60 transition-colors hover:text-foreground/80">
                                <Shield className="h-4 w-4" />
                                <span>Admin</span>
                            </Link>
                        )}
                        <AuthMenu />
                    </div>
                </div>
            </div>
        </nav>
    );
}
