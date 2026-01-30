"use client";

import React from "react";

interface Thought {
    id: string;
    agent: string;
    text: string;
    type: "strategy" | "insight" | "warning";
}

interface InnerMonologueProps {
    thoughts: Thought[];
}

export default function InnerMonologue({ thoughts }: InnerMonologueProps) {
    return (
        <div className="p-4 rounded-xl bg-gray-900/50 border border-gray-800 backdrop-blur-md flex flex-col h-[400px]">
            <h3 className="text-sm font-medium text-gray-400 mb-4 tracking-wider uppercase">Strategic Inner Monologue</h3>

            <div className="flex-1 overflow-y-auto space-y-3 pr-2 scrollbar-thin scrollbar-thumb-gray-800 scrollbar-track-transparent">
                {thoughts.map((thought) => (
                    <div
                        key={thought.id}
                        className={`p-3 rounded-lg border text-xs font-mono ${thought.agent === "Juma"
                            ? "bg-orange-500/5 border-orange-500/20"
                            : "bg-blue-500/5 border-blue-500/20"
                            }`}
                    >
                        <div className="flex justify-between items-center mb-1">
                            <span className={`font-bold ${thought.agent === "Juma" ? "text-orange-400" : "text-blue-400"}`}>
                                [{thought.agent.toUpperCase()}]
                            </span>
                            <span className="text-[10px] text-gray-600 uppercase italic">{thought.type}</span>
                        </div>
                        <p className="text-gray-300 leading-relaxed italic">&quot;{thought.text}&quot;</p>
                    </div>
                ))}
            </div>
        </div>
    );
}
