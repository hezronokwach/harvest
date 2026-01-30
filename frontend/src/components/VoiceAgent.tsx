"use client";

import React, { useEffect } from "react";
import { VoiceProvider, useVoice } from "@humeai/voice-react";

interface EmotionScores {
    confidence: number;
    stress: number;
    urgency: number;
}

interface VoiceAgentProps {
    configId: string;
    name: string;
    onEmotionsUpdate: (emotions: EmotionScores) => void;
    onThoughtUpdate: (thought: string) => void;
    accessToken: string;
}

function InnerAgent({ name, onEmotionsUpdate, onThoughtUpdate }: { name: string, onEmotionsUpdate: (emotions: EmotionScores) => void, onThoughtUpdate: (thought: string) => void }) {
    const { connect, status, lastVoiceMessage } = useVoice();

    useEffect(() => {
        if (lastVoiceMessage) {
            const prosody = (lastVoiceMessage as any).models?.prosody?.scores;
            if (prosody) {
                onEmotionsUpdate({
                    confidence: prosody.Confidence || 0.5,
                    stress: prosody.Stress || 0.1,
                    urgency: prosody.Urgency || 0.2
                });
            }

            if (lastVoiceMessage.type === "assistant_message") {
                onThoughtUpdate(lastVoiceMessage.message.content || "");
            }
        }
    }, [lastVoiceMessage, onEmotionsUpdate, onThoughtUpdate]);

    return (
        <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${status.value === "connected" ? "bg-green-500" : "bg-red-500"}`} />
            <span className="text-[10px] font-mono uppercase text-gray-400">{name}: {status.value}</span>
            {status.value === "disconnected" && (
                <button
                    onClick={() => (connect as any)()}
                    className="text-[10px] bg-white/5 hover:bg-white/10 px-2 py-0.5 rounded border border-white/10 transition-colors"
                >
                    RECONNECT
                </button>
            )}
        </div>
    );
}

export default function VoiceAgent({ configId, name, onEmotionsUpdate, onThoughtUpdate, accessToken }: VoiceAgentProps) {
    const Provider = VoiceProvider as any;
    return (
        <Provider
            auth={{ type: "accessToken", value: accessToken }}
            configId={configId}
        >
            <InnerAgent name={name} onEmotionsUpdate={onEmotionsUpdate} onThoughtUpdate={onThoughtUpdate} />
        </Provider>
    );
}
