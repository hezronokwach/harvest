"use client";

import React, { useState, useEffect } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useParticipants,
  useRoomContext,
} from "@livekit/components-react";
import { Participant } from "livekit-client";
import EmotionRadar from "@/components/EmotionRadar";
import PriceGapSlider from "@/components/PriceGapSlider";
import InnerMonologue from "@/components/InnerMonologue";
import Transcript from "@/components/Transcript";
import { RoomEvent, TranscriptionSegment, TrackPublication, RemoteParticipant } from "livekit-client";

interface Emotions {
  confidence: number;
  stress: number;
  urgency: number;
}

interface Thought {
  id: string;
  agent: string;
  text: string;
  type: "strategy" | "insight" | "warning";
}

interface Negotiation {
  ask: number;
  bid: number;
  target: number;
}

export default function Home() {
  const [lkToken, setLkToken] = useState<string | null>(null);
  const [hasMounted, setHasMounted] = useState(false);
  const [inRoom, setInRoom] = useState(false);

  const [halimaEmotions, setHalimaEmotions] = useState({ confidence: 0.85, stress: 0.12, urgency: 0.45 });
  const [alexEmotions, setAlexEmotions] = useState({ confidence: 0.62, stress: 0.78, urgency: 0.92 });

  const [negotiation] = useState({ ask: 1.25, bid: 0.95, target: 1.15 });
  const [negotiationProgress, setNegotiationProgress] = useState(0);
  const [thoughts, setThoughts] = useState<Array<{ id: string; agent: string; text: string; type: "strategy" | "insight" | "warning" }>>([]);
  const [transcripts, setTranscripts] = useState<Array<{ id: string; agent: string; text: string }>>([]);

  useEffect(() => {
    const timer = setTimeout(() => setHasMounted(true), 0);
    return () => clearTimeout(timer);
  }, []);

  const [barHeights, setBarHeights] = useState<{ orange: number[], blue: number[] }>({ orange: [], blue: [] });

  useEffect(() => {
    const timer = setTimeout(() => {
      setBarHeights({
        orange: [...Array(20)].map(() => Math.random() * 60 + 20),
        blue: [...Array(20)].map(() => Math.random() * 60 + 20)
      });
    }, 0);
    return () => clearTimeout(timer);
  }, []);

  const enterWarRoom = async () => {
    try {
      const resp = await fetch(`http://localhost:8000/livekit/token?participant_name=Observer_${Math.random().toString(36).substring(7)}`);
      const data = await resp.json();
      setLkToken(data.token);
      setInRoom(true);
    } catch (e) {
      console.error("Failed to join room:", e);
    }
  };

  const dispatchAgents = async () => {
    try {
      await fetch("http://localhost:8000/livekit/dispatch", { method: "POST" });
    } catch (e) {
      console.error("Failed to dispatch agents:", e);
    }
  };

  if (!hasMounted) return null;

  return (
    <main className="min-h-screen bg-[#050505] text-white p-8 font-sans selection:bg-orange-500/30">
      {!inRoom ? (
        <div className="max-w-7xl mx-auto h-[80vh] flex flex-col items-center justify-center text-center">
          <h1 className="text-6xl font-black tracking-tighter uppercase italic mb-4">
            <span className="text-orange-500">Echo</span>Yield
          </h1>
          <p className="text-gray-400 mb-8 max-w-md">Secure agricultural negotiation at high frequency. Connect to the digital war room.</p>
          <button
            onClick={enterWarRoom}
            className="px-8 py-4 bg-orange-500 text-black font-black uppercase tracking-widest rounded-full hover:scale-105 transition-transform"
          >
            Enter War Room
          </button>
        </div>
      ) : (
        <LiveKitRoom
          token={lkToken || ""}
          serverUrl={process.env.NEXT_PUBLIC_LIVEKIT_URL}
          connect={true}
          onConnected={() => {
            console.log("Connected to LiveKit!");
          }}
          className="h-full"
        >
          <DashboardContent
            negotiation={negotiation}
            negotiationProgress={negotiationProgress}
            setNegotiationProgress={setNegotiationProgress}
            halimaEmotions={halimaEmotions}
            alexEmotions={alexEmotions}
            setHalimaEmotions={setHalimaEmotions}
            setAlexEmotions={setAlexEmotions}
            barHeights={barHeights}
            thoughts={thoughts}
            setThoughts={setThoughts}
            transcripts={transcripts}
            setTranscripts={setTranscripts}
            hasMounted={hasMounted}
          />
          <RoomAudioRenderer />
        </LiveKitRoom>
      )}
    </main>
  );
}

function DashboardContent({
  negotiation,
  negotiationProgress,
  setNegotiationProgress,
  halimaEmotions,
  alexEmotions,
  setHalimaEmotions,
  setAlexEmotions,
  barHeights,
  thoughts,
  setThoughts,
  transcripts,
  setTranscripts,
  hasMounted
}: {
  negotiation: Negotiation;
  negotiationProgress: number;
  setNegotiationProgress: React.Dispatch<React.SetStateAction<number>>;
  halimaEmotions: Emotions;
  alexEmotions: Emotions;
  setHalimaEmotions: React.Dispatch<React.SetStateAction<Emotions>>;
  setAlexEmotions: React.Dispatch<React.SetStateAction<Emotions>>;
  barHeights: { orange: number[]; blue: number[] };
  thoughts: Thought[];
  setThoughts: React.Dispatch<React.SetStateAction<Thought[]>>;
  transcripts: Array<{ id: string; agent: string; text: string }>;
  setTranscripts: React.Dispatch<React.SetStateAction<Array<{ id: string; agent: string; text: string }>>>;
  hasMounted: boolean;
}) {
  const participants = useParticipants();
  const room = useRoomContext();
  const [agentStatus, setAgentStatus] = useState({ halima: false, alex: false });

  useEffect(() => {
    if (!room) return;

    const onDataReceived = (payload: Uint8Array, participant?: Participant) => {
      const decoder = new TextDecoder();
      try {
        const data = JSON.parse(decoder.decode(payload));
        const agentName = participant?.name ||
          (participant?.identity.includes("halima") || participant?.identity.includes("juma") ? "Halima" : "Alex");

        if (data.type === "thought") {
          const id = `${agentName}-${crypto.randomUUID()}`;
          setThoughts((prev: Thought[]) => [
            { id, agent: agentName, text: data.text, type: "insight" },
            ...prev.slice(0, 9),
          ]);

          // Dynamic Emotion Detection
          const emotionMap: Record<string, Emotions> = {
            'confident': { confidence: 0.9, stress: 0.1, urgency: 0.3 },
            'concerned': { confidence: 0.4, stress: 0.6, urgency: 0.5 },
            'frustrated': { confidence: 0.3, stress: 0.8, urgency: 0.7 },
            'hopeful': { confidence: 0.7, stress: 0.2, urgency: 0.4 },
            'satisfied': { confidence: 0.95, stress: 0.05, urgency: 0.1 },
            'respectful': { confidence: 0.8, stress: 0.1, urgency: 0.2 }
          };

          const detectedKey = Object.keys(emotionMap).find(key => data.text.toLowerCase().includes(key));
          if (detectedKey) {
            if (agentName === "Halima") setHalimaEmotions(emotionMap[detectedKey]);
            if (agentName === "Alex") setAlexEmotions(emotionMap[detectedKey]);
          }

        } else if (data.type === "emotions") {
          const emotions = {
            confidence: data.confidence ?? 0.5,
            stress: data.stress ?? 0.1,
            urgency: data.urgency ?? 0.2
          };
          if (agentName === "Halima") setHalimaEmotions(emotions);
          if (agentName === "Alex") setAlexEmotions(emotions);
        } else if (data.type === "round_update") {
          const progress = (data.round / 8) * 100;
          setNegotiationProgress(progress);
        }
      } catch (e) {
        console.error("Failed to parse data message:", e);
      }
    };

    const handleTranscription = (segments: TranscriptionSegment[], participant?: Participant) => {
      segments.forEach(segment => {
        if (segment.final && participant) {
          const agentName = participant.identity.includes('halima') || participant.identity.includes('juma') ? 'Halima' : 'Alex';
          setTranscripts(prev => [
            { id: crypto.randomUUID(), agent: agentName, text: segment.text },
            ...prev.slice(0, 19)
          ]);
        }
      });
    };

    const handleConnected = (participant: RemoteParticipant) => {
      if (participant.identity.includes('halima') || participant.identity.includes('juma')) {
        setAgentStatus(prev => ({ ...prev, halima: true }));
      } else if (participant.identity.includes('alex')) {
        setAgentStatus(prev => ({ ...prev, alex: true }));
      }
    };

    const handleDisconnected = (participant: RemoteParticipant) => {
      if (participant.identity.includes('halima') || participant.identity.includes('juma')) {
        setAgentStatus(prev => ({ ...prev, halima: false }));
      } else if (participant.identity.includes('alex')) {
        setAgentStatus(prev => ({ ...prev, alex: false }));
      }
    };

    room.on(RoomEvent.DataReceived, onDataReceived);
    room.on(RoomEvent.TranscriptionReceived, handleTranscription);
    room.on(RoomEvent.ParticipantConnected, handleConnected);
    room.on(RoomEvent.ParticipantDisconnected, handleDisconnected);

    // Initial status check
    room.remoteParticipants.forEach(p => {
      if (p.identity.includes('halima') || p.identity.includes('juma')) setAgentStatus(prev => ({ ...prev, halima: true }));
      if (p.identity.includes('alex')) setAgentStatus(prev => ({ ...prev, alex: true }));
    });

    return () => {
      room.off(RoomEvent.DataReceived, onDataReceived);
      room.off(RoomEvent.TranscriptionReceived, handleTranscription);
      room.off(RoomEvent.ParticipantConnected, handleConnected);
      room.off(RoomEvent.ParticipantDisconnected, handleDisconnected);
    };
  }, [room, setThoughts, setTranscripts, setHalimaEmotions, setAlexEmotions, setNegotiationProgress]);

  // Filtering for agents
  const halima = participants.find(p => p.identity.includes("halima") || p.identity.includes("juma") || p.name?.toLowerCase().includes("halima"));
  const alex = participants.find(p => p.identity.includes("alex") || p.name?.toLowerCase().includes("alex"));

  return (
    <div className="max-w-7xl mx-auto">
      <div className="flex justify-between items-center mb-12 border-b border-white/5 pb-8">
        <div>
          <h1 className="text-3xl font-black tracking-tighter uppercase italic flex items-center gap-2">
            <span className="text-orange-500">Echo</span>Yield
            <span className="bg-orange-500 text-black text-[10px] px-1.5 py-0.5 not-italic tracking-normal rounded font-bold">ALPHA</span>
          </h1>
          <p className="text-gray-500 text-xs mt-1 uppercase tracking-widest">Farmer&apos;s Strategic Negotiation Hub</p>
        </div>
        <div className="flex gap-6 items-center">
          <button
            onClick={async () => {
              try {
                await fetch("http://localhost:8000/livekit/dispatch", { method: "POST" });
              } catch (e) {
                console.error("Dispatch failed:", e);
              }
            }}
            className="px-4 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg text-[10px] font-black uppercase tracking-widest transition-all hover:scale-105 active:scale-95"
          >
            Deploy Agents
          </button>
          <div className="flex gap-4 pr-6 border-r border-white/10 items-center">
            <div className="flex items-center gap-2">
              <div
                className={`w-2 h-2 rounded-full transition-all duration-300 ${agentStatus.halima
                  ? `bg-green-500 shadow-[0_0_12px_rgba(34,197,94,0.6)] ${halima?.isSpeaking ? "animate-pulse scale-150" : ""}`
                  : "bg-red-500/30 grayscale"
                  }`}
              />
              <span className={`text-[10px] font-mono uppercase transition-colors ${agentStatus.halima ? "text-gray-300" : "text-gray-600"}`}>
                HALIMA: {agentStatus.halima ? (halima?.isSpeaking ? "SPEAKING" : "ACTIVE") : "OFFLINE"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div
                className={`w-2 h-2 rounded-full transition-all duration-300 ${agentStatus.alex
                  ? `bg-green-500 shadow-[0_0_12px_rgba(34,197,94,0.6)] ${alex?.isSpeaking ? "animate-pulse scale-150" : ""}`
                  : "bg-red-500/30 grayscale"
                  }`}
              />
              <span className={`text-[10px] font-mono uppercase transition-colors ${agentStatus.alex ? "text-gray-300" : "text-gray-600"}`}>
                ALEX: {agentStatus.alex ? (alex?.isSpeaking ? "SPEAKING" : "ACTIVE") : "OFFLINE"}
              </span>
            </div>
          </div>
          <div className="text-right">
            <p className="text-[10px] text-gray-600 uppercase">LiveKit Room</p>
            <p className="text-xs font-mono text-green-500 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" /> BARN_ROOM_01
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-8">
        <div className="col-span-8 space-y-8">
          <PriceGapSlider
            ask={negotiation.ask}
            bid={negotiation.bid}
            target={negotiation.target}
          />

          <div className="grid grid-cols-2 gap-8">
            <div className="transition-all duration-700 p-1 rounded-2xl" style={{
              background: halimaEmotions.stress > 0.5 ? 'rgba(239, 68, 68, 0.1)' : halimaEmotions.confidence > 0.8 ? 'rgba(249, 115, 22, 0.1)' : 'transparent',
              boxShadow: halimaEmotions.stress > 0.6 ? '0 0 40px rgba(239, 68, 68, 0.05)' : halimaEmotions.confidence > 0.8 ? '0 0 40px rgba(249, 115, 22, 0.05)' : 'none'
            }}>
              <EmotionRadar agentName="Halima (Seller)" scores={halimaEmotions} color="#f97316" />
            </div>
            <div className="transition-all duration-700 p-1 rounded-2xl" style={{
              background: alexEmotions.stress > 0.5 ? 'rgba(239, 68, 68, 0.1)' : alexEmotions.confidence > 0.8 ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
              boxShadow: alexEmotions.stress > 0.6 ? '0 0 40px rgba(239, 68, 68, 0.05)' : alexEmotions.confidence > 0.8 ? '0 0 40px rgba(59, 130, 246, 0.05)' : 'none'
            }}>
              <EmotionRadar agentName="Alex (Buyer)" scores={alexEmotions} color="#3b82f6" />
            </div>
          </div>

          <div className="h-48 rounded-xl bg-gradient-to-t from-orange-500/5 to-transparent border border-white/5 flex flex-col items-center justify-center relative overflow-hidden">
            <div className="absolute top-4 left-4 flex items-center gap-3">
              <div className="text-[8px] font-bold text-orange-500 uppercase tracking-[0.3em]">Negotiation Waveform</div>
              <div className="flex gap-0.5">
                {[...Array(3)].map((_, i) => (
                  <div key={i} className="w-0.5 h-2 bg-orange-500/20 animate-pulse" style={{ animationDelay: `${i * 0.2}s` }} />
                ))}
              </div>
            </div>

            <div className="flex gap-1 items-center z-10">
              {hasMounted && barHeights.orange.map((height: number, i: number) => (
                <div
                  key={i}
                  className={`w-1 bg-orange-500/40 rounded-full ${halima?.isSpeaking ? "animate-bounce" : ""}`}
                  style={{
                    height: `${halima?.isSpeaking ? height : 10}%`,
                    animationDelay: `${i * 0.1}s`,
                    transition: "height 0.2s ease"
                  }}
                />
              ))}
              <div className="mx-8 flex flex-col items-center">
                <p className="text-[10px] font-mono text-gray-600 uppercase tracking-[0.2em] mb-2">Live Audio Matrix</p>
                <div className="w-32 h-[2px] bg-white/5 rounded-full relative">
                  <div
                    className="absolute top-0 left-0 h-full bg-orange-500 transition-all duration-500 ease-out"
                    style={{ width: `${negotiationProgress}%` }}
                  />
                </div>
                <p className="mt-2 text-[8px] font-mono text-orange-500/50 uppercase tracking-widest">Efficiency: {Math.round(negotiationProgress)}%</p>
              </div>
              {hasMounted && barHeights.blue.map((height: number, i: number) => (
                <div
                  key={i}
                  className={`w-1 bg-blue-500/40 rounded-full ${alex?.isSpeaking ? "animate-bounce" : ""}`}
                  style={{
                    height: `${alex?.isSpeaking ? height : 10}%`,
                    animationDelay: `${i * 0.1}s`,
                    transition: "height 0.2s ease"
                  }}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="col-span-4 space-y-8">
          <InnerMonologue thoughts={thoughts} />

          <Transcript transcripts={transcripts} />

          <div className="p-6 rounded-xl bg-gradient-to-br from-orange-500 to-orange-600 text-black shadow-lg shadow-orange-500/20 active:scale-[0.98] transition-all">
            <h4 className="text-[10px] font-black uppercase mb-1 opacity-60 tracking-widest">Current Strategy Directive</h4>
            <p className="text-lg font-bold leading-tight italic tracking-tight">&quot;Focus on high-quality logistics. Emphasize Halima&apos;s reliable supply chain.&quot;</p>
            <div className="mt-4 flex gap-2">
              <span className="text-[8px] font-black uppercase border border-black/20 px-2 py-0.5 rounded tracking-tighter bg-black/5">Tactical Empathy</span>
              <span className="text-[8px] font-black uppercase border border-black/20 px-2 py-0.5 rounded tracking-tighter bg-black/5">Supply Anchoring</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
