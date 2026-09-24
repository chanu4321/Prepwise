import Link from "next/link";
import { BookOpen } from "lucide-react";

const REPO_URL = "https://github.com/chanu4321/Prepwise";

// lucide's brand icons are deprecated, so the GitHub mark is inlined
function GithubIcon({ className }: { className?: string }) {
    return (
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" className={className}>
            <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
        </svg>
    );
}

export function Footer() {
    return (
        <footer className="border-t-2 border-primary/50 bg-footer text-footer-foreground">
            <div className="container mx-auto max-w-6xl px-4 py-10">
                <div className="grid grid-cols-2 gap-8 md:grid-cols-3">
                    <div className="col-span-2 md:col-span-1">
                        <Link href="/" className="flex items-center gap-2 font-bold text-lg mb-3">
                            <BookOpen className="h-5 w-5 text-primary" />
                            PrepWise
                        </Link>
                        <p className="text-xs text-footer-foreground/60 leading-relaxed max-w-56">
                            A searchable archive of past question papers, with AI mock-paper generation grounded in them.
                        </p>
                    </div>
                    <div>
                        <h4 className="text-sm font-semibold mb-3">Product</h4>
                        <ul className="space-y-2 text-sm text-footer-foreground/70">
                            <li><Link href="/papers" className="hover:text-primary transition-colors">Browse Papers</Link></li>
                            <li><Link href="/generate" className="hover:text-primary transition-colors">Generate Paper</Link></li>
                            <li><Link href="/syllabus" className="hover:text-primary transition-colors">Syllabus Manager</Link></li>
                            <li><Link href="/upload" className="hover:text-primary transition-colors">Upload Papers</Link></li>
                        </ul>
                    </div>
                    <div>
                        <h4 className="text-sm font-semibold mb-3">Project</h4>
                        <ul className="space-y-2 text-sm text-footer-foreground/70">
                            <li>
                                <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 hover:text-primary transition-colors">
                                    <GithubIcon className="h-4 w-4" />
                                    GitHub
                                </a>
                            </li>
                            <li><a href={`${REPO_URL}#readme`} target="_blank" rel="noopener noreferrer" className="hover:text-primary transition-colors">Documentation</a></li>
                            <li><a href={`${REPO_URL}/issues`} target="_blank" rel="noopener noreferrer" className="hover:text-primary transition-colors">Report an issue</a></li>
                        </ul>
                    </div>
                </div>
                <div className="mt-10 border-t border-footer-foreground/10 pt-6 flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-footer-foreground/50">
                    <span>© {new Date().getFullYear()} PrepWise</span>
                    <span>Built with Next.js · FastAPI · Qdrant · NVIDIA NIM</span>
                </div>
            </div>
        </footer>
    );
}
