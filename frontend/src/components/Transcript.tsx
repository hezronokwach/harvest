import React, { useEffect, useRef } from "react";

interface Transcript {
    id: string;
    agent: string;
    text: string;
}

interface TranscriptProps {
    transcripts: Transcript[];
}

export default function Transcript({ transcripts }: TranscriptProps) {
    const scrollRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [transcripts]);

    return (
        <div className="bg-[#0a0a0a] border border-white/5 rounded-xl overflow-hidden flex flex-col h-full">
            <div className="p-4 border-b border-white/5 bg-white/5 flex justify-between items-center">
                <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-orange-500">Live Negotiation Transcript</h3>
                <div className="flex gap-1.5">
                    <div className="w-1 h-1 rounded-full bg-orange-500 animate-pulse" />
                    <div className="w-1 h-1 rounded-full bg-orange-500/50 animate-pulse delay-75" />
                    <div className="w-1 h-1 rounded-full bg-orange-500/20 animate-pulse delay-150" />
                </div>
            </div>

            <div
                ref={scrollRef}
                className="p-4 space-y-4 overflow-y-auto max-h-[300px] scrollbar-thin scrollbar-track-transparent scrollbar-thumb-white/10"
            >
                {transcripts.length === 0 ? (
                    <div className="h-20 flex items-center justify-center">
                        <p className="text-[10px] font-mono text-gray-600 uppercase tracking-widest">Waiting for dialogue...</p>
                    </div>
                ) : (
                    transcripts.map((t) => (
                        <div key={t.id} className="fade-in">
                            <div className="flex items-center gap-2 mb-1">
                                <span className={`text-[9px] font-black uppercase tracking-tighter px-1.5 py-0.5 rounded ${t.agent === "Halima" ? "bg-orange-500 text-black" : "bg-blue-600 text-white"
                                    }`}>
                                    {t.agent}
                                </span>
                                <span className="text-[8px] font-mono text-gray-500 uppercase">
                                    {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                                </span>
                            </div>
                            <p className="text-sm font-medium text-gray-300 leading-relaxed pl-1 italic border-l border-white/10">
                                &quot;{t.text}&quot;
                            </p>
                        </div>
                    ))
                )}
            </div>

            <style jsx>{`
        .fade-in {
          animation: slideIn 0.3s ease-out forwards;
        }
        @keyframes slideIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
        </div>
    );
}
