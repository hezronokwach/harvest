"use client";

import React, { useEffect } from "react";
import { useVoice } from "@humeai/voice-react";

interface EmotionScores {
    confidence: number;
    stress: number;
    urgency: number;
}

export function VoiceAgentStatus({ name, onEmotionsUpdate, configId, accessToken }: {
    name: string,
    onEmotionsUpdate: (emotions: EmotionScores) => void,
    configId: string,
    accessToken: string
}) {
    const { connect, status, lastVoiceMessage, lastUserMessage, error, disconnect } = useVoice();

    useEffect(() => {
        console.log(`[VoiceAgentStatus:${name}] Status update:`, status.value);
        if (error) {
            console.error(`[VoiceAgentStatus:${name}] SDK Error:`, error);
        }
    }, [status.value, name, error]);

    useEffect(() => {
        if (lastVoiceMessage) {
            const prosody = (lastVoiceMessage as { models?: { prosody?: { scores: Record<string, number> } } }).models?.prosody?.scores;
            if (prosody) {
                onEmotionsUpdate({
                    confidence: prosody.Confidence || 0.5,
                    stress: prosody.Stress || 0.1,
                    urgency: prosody.Urgency || 0.2
                });
            }
            console.log(`[VoiceAgentStatus:${name}] Agent spoke:`, lastVoiceMessage.message.content);
        }
    }, [lastVoiceMessage, onEmotionsUpdate, name]);

    useEffect(() => {
        if (lastUserMessage) {
            console.log(`[VoiceAgentStatus:${name}] Heard something:`, lastUserMessage.message.content);
        }
    }, [lastUserMessage, name]);

    const handleToggle = async () => {
        if (status.value === "connected") {
            disconnect();
        } else {
            console.log(`[VoiceAgentStatus:${name}] Connecting with config: ${configId}`);
            try {
                await connect({
                    auth: { type: "accessToken", value: accessToken },
                    configId: configId,
                });
            } catch (err) {
                console.error(`[VoiceAgentStatus:${name}] Connection failed:`, err);
            }
        }
    };

    return (
        <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${status.value === "connected" ? "bg-green-500" : status.value === "connecting" ? "bg-yellow-500 animate-pulse" : "bg-red-500"}`} />
                <span className="text-[10px] font-mono uppercase text-gray-400">Hub ({name}): {status.value}</span>
                <button
                    onClick={handleToggle}
                    className="text-[10px] bg-white/5 hover:bg-white/10 px-2 py-0.5 rounded border border-white/10 transition-colors"
                >
                    {status.value === "connected" ? "DISCONNECT" : "CONNECT"}
                </button>
            </div>
            {status.value === "connecting" && (
                <span className="text-[8px] text-yellow-400 italic">Check browser microphone permission...</span>
            )}
            {status.value === "error" && error && (
                <span className="text-[8px] text-red-400 max-w-[150px] leading-tight opacity-70">
                    ERR: {error.message || "Unknown error"}
                </span>
            )}
        </div>
    );
}

const VoiceAgent = VoiceAgentStatus;
export default VoiceAgent;
