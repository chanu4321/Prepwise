import Link from "next/link";
import { BookOpen, Upload } from "lucide-react";

export function Navbar() {
    return (
        <nav className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 sticky top-0 z-50">
            <div className="container flex h-14 max-w-screen-2xl items-center px-4 md:px-8">
                <Link href="/" className="mr-6 flex items-center space-x-2">
                    <BookOpen className="h-6 w-6 text-primary" />
                    <span className="hidden font-bold sm:inline-block">Academic Repo</span>
                </Link>
                <div className="flex flex-1 items-center justify-between space-x-2 md:justify-end">
                    <div className="flex items-center space-x-6 text-sm font-medium">
                        <Link
                            href="/upload"
                            className="flex items-center space-x-2 text-foreground/60 transition-colors hover:text-foreground/80"
                        >
                            <Upload className="h-4 w-4" />
                            <span>Upload</span>
                        </Link>
                        <Link
                            href="/papers"
                            className="text-foreground/60 transition-colors hover:text-foreground/80"
                        >
                            Browse
                        </Link>
                    </div>
                </div>
            </div>
        </nav>
    );
}
