import Link from "next/link";
import { Search, FileText, Brain, Zap } from "lucide-react";

export default function Home() {
  return (
    <div className="flex min-h-[calc(100vh-3.5rem)] flex-col">
      <section className="space-y-6 pb-8 pt-6 md:pb-12 md:pt-10 lg:py-32">
        <div className="container flex max-w-[64rem] flex-col items-center gap-4 text-center">
          <Link
            href="/upload"
            className="rounded-2xl bg-muted px-4 py-1.5 text-sm font-medium hover:bg-muted/80"
          >
            🚀 Try our new AI Uploader
          </Link>
          <h1 className="font-heading text-3xl font-bold sm:text-5xl md:text-6xl lg:text-7xl bg-gradient-to-r from-primary to-blue-600 bg-clip-text text-transparent pb-2">
            Ace Your Exams with AI
          </h1>
          <p className="max-w-[42rem] leading-normal text-muted-foreground sm:text-xl sm:leading-8">
            The smartest way to access university question papers.
            Automated OCR, semantic search, and AI-generated mock papers at your fingertips.
          </p>
          <div className="w-full max-w-sm space-y-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <input
                type="search"
                placeholder="Search for subjects, papers..."
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 pl-9 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Try searching for "Computer Networks 2024" or "Data Structures"
            </p>
          </div>
        </div>
      </section>

      <section className="container space-y-6 bg-slate-50 py-8 dark:bg-transparent md:py-12 lg:py-24">
        <div className="mx-auto flex max-w-[58rem] flex-col items-center space-y-4 text-center">
          <h2 className="font-heading text-3xl leading-[1.1] sm:text-3xl md:text-6xl">
            Features
          </h2>
          <p className="max-w-[85%] leading-normal text-muted-foreground sm:text-lg sm:leading-7">
            Built for students, powered by advanced technology.
          </p>
        </div>
        <div className="mx-auto grid justify-center gap-4 sm:grid-cols-2 md:max-w-[64rem] md:grid-cols-3">
          <div className="relative overflow-hidden rounded-lg border bg-background p-2">
            <div className="flex h-[180px] flex-col justify-between rounded-md p-6">
              <FileText className="h-12 w-12 text-primary" />
              <div className="space-y-2">
                <h3 className="font-bold">Automated OCR</h3>
                <p className="text-sm text-muted-foreground">
                  Upload PDFs or images. We automatically extract and digitize the text for you.
                </p>
              </div>
            </div>
          </div>
          <div className="relative overflow-hidden rounded-lg border bg-background p-2">
            <div className="flex h-[180px] flex-col justify-between rounded-md p-6">
              <Brain className="h-12 w-12 text-primary" />
              <div className="space-y-2">
                <h3 className="font-bold">Semantic Search</h3>
                <p className="text-sm text-muted-foreground">
                  Don't just keyword match. Find questions based on meaning and context.
                </p>
              </div>
            </div>
          </div>
          <div className="relative overflow-hidden rounded-lg border bg-background p-2">
            <div className="flex h-[180px] flex-col justify-between rounded-md p-6">
              <Zap className="h-12 w-12 text-primary" />
              <div className="space-y-2">
                <h3 className="font-bold">AI Mock Papers</h3>
                <p className="text-sm text-muted-foreground">
                  Generate personalized mock exams based on previous year trends.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}