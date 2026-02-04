"use client";

import React, { useState, useEffect } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useTracks,
  useRoomContext,
} from "@livekit/components-react";
import { Participant, Track } from "livekit-client";
import Transcript from "@/components/Transcript";
import { RoomEvent, TranscriptionSegment, TrackPublication, RemoteParticipant } from "livekit-client";

interface Timeline {
  turn: number;
  round: number;
  maxRounds: number;
}

interface Thought {
  id: string;
  agent: string;
  text: string;
  type: "strategy" | "insight" | "warning";
}

// Suppress non-critical LiveKit track warnings in development
if (typeof window !== "undefined") {
  const originalConsoleError = console.error;
  console.error = (...args) => {
    if (
      typeof args[0] === "string" &&
      args[0].includes("Tried to add a track for a participant")
    ) {
      return;
    }
    originalConsoleError(...args);
  };
}

export default function Home() {
  const [lkToken, setLkToken] = useState<string | null>(null);
  const [hasMounted, setHasMounted] = useState(false);
  const [inRoom, setInRoom] = useState(false);
  const [persona, setPersona] = useState<string | null>(null);
  const [meetingId, setMeetingId] = useState<string>("HARVEST_DEAL_1");


  const [timeline, setTimeline] = useState<Timeline>({ turn: 0, round: 0, maxRounds: 8 });
  const [negotiationProgress, setNegotiationProgress] = useState(0);
  const [barHeights, setBarHeights] = useState({ orange: [0, 0, 0], blue: [0, 0, 0] });
  const [thoughts, setThoughts] = useState<Array<{ id: string; agent: string; text: string; type: "strategy" | "insight" | "warning" }>>([]);
  const [transcripts, setTranscripts] = useState<Array<{ id: string; agent: string; text: string }>>([]);
  const [callStatus, setCallStatus] = useState<string>("");


  useEffect(() => {
    const timer = setTimeout(() => setHasMounted(true), 0);
    return () => clearTimeout(timer);
  });

  useEffect(() => {
    setBarHeights({
      orange: [...Array(20)].map(() => Math.random() * 60 + 20),
      blue: [...Array(20)].map(() => Math.random() * 60 + 20)
    });
  }, []);

  const enterPresenceRoom = async (p: string) => {
    try {
      setPersona(p);
      const resp = await fetch(`http://localhost:8000/livekit/token?participant_name=User_${p}&persona=${p}`);
      const data = await resp.json();
      setLkToken(data.token);
      setInRoom(true);
    } catch (e) {
      console.error("Failed to join presence room:", e);
    }
  };

  const startNegotiation = async () => {
    try {
      const callRoom = `call-${meetingId.toLowerCase().replace(/\s+/g, "_")}`;
      const resp = await fetch(`http://localhost:8000/negotiation/call?room_name=${callRoom}`, { method: "POST" });
      const data = await resp.json();

      if (data.status === "already_running") {
        console.log("Call already in progress, joining...");
        setCallStatus("Joining ongoing negotiation...");
      } else {
        console.log("Call started:", data);
        setCallStatus("Starting negotiation...");
      }

      // Force disconnect from presence room
      setInRoom(false);

      // Wait a moment for disconnect to complete
      await new Promise(resolve => setTimeout(resolve, 500));

      // Get a new token for the shared call room
      const tokenResp = await fetch(`http://localhost:8000/livekit/token?participant_name=User_${persona}&persona=${persona}&room_name=${callRoom}`);
      const tokenData = await tokenResp.json();

      // Reconnect with new token to call room
      setLkToken(tokenData.token);
      setInRoom(true);

      // Clear status after 3 seconds
      setTimeout(() => setCallStatus(""), 3000);
    } catch (e) {
      console.error("Failed to start negotiation:", e);
      setCallStatus("Failed to start negotiation");
      setTimeout(() => setCallStatus(""), 3000);
    }
  };


  if (!hasMounted) return null;

  return (
    <main className="min-h-screen bg-[#050505] text-white p-8 font-sans selection:bg-orange-500/30">
      {!inRoom ? (
        <div className="max-w-7xl mx-auto h-[80vh] flex flex-col items-center justify-center text-center">
          <h1 className="text-6xl font-black tracking-tighter uppercase italic mb-4">
            <span className="text-orange-500">Harvest</span>
          </h1>
          <p className="text-gray-400 mb-8 max-w-md">Stateless decentralized agents for agricultural negotiation. Select your role to begin.</p>

          <div className="mb-12 w-full max-w-xs mx-auto">
            <label className="block text-[10px] uppercase font-black tracking-widest text-gray-500 mb-2">Meeting ID</label>
            <input
              type="text"
              value={meetingId}
              onChange={(e) => setMeetingId(e.target.value)}
              className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-center font-mono text-orange-500 focus:outline-none focus:border-orange-500/50 transition-colors"
              placeholder="e.g. DEAL_FEB"
            />
          </div>

          <div className="flex gap-8">
            <button
              onClick={() => enterPresenceRoom("Halima")}
              className="group relative px-12 py-6 bg-orange-500 rounded-2xl hover:scale-105 transition-all text-left"
            >
              <span className="block text-black font-black uppercase tracking-widest text-xl">Halima</span>
              <span className="block text-black/60 text-xs font-bold uppercase tracking-tight">The Seller (Farmer)</span>
              <div className="absolute -inset-2 bg-orange-500/20 blur-xl opacity-0 group-hover:opacity-100 transition-opacity" />
            </button>

            <button
              onClick={() => enterPresenceRoom("Alex")}
              className="group relative px-12 py-6 bg-white/5 border border-white/10 rounded-2xl hover:scale-105 transition-all text-left"
            >
              <span className="block text-white font-black uppercase tracking-widest text-xl">Alex</span>
              <span className="block text-white/40 text-xs font-bold uppercase tracking-tight">The Buyer (Commodity Agent)</span>
              <div className="absolute -inset-2 bg-white/5 blur-xl opacity-0 group-hover:opacity-100 transition-opacity" />
            </button>
          </div>
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
            persona={persona}
            negotiationProgress={negotiationProgress}
            setNegotiationProgress={setNegotiationProgress}
            timeline={timeline}
            setTimeline={setTimeline}
            barHeights={barHeights}
            thoughts={thoughts}
            setThoughts={setThoughts}
            transcripts={transcripts}
            setTranscripts={setTranscripts}
            hasMounted={hasMounted}
            onStartNegotiation={startNegotiation}
            callStatus={callStatus}
          />
          <RoomAudioRenderer />
        </LiveKitRoom>
      )}
    </main>
  );
}

function DashboardContent({
  persona,
  negotiationProgress,
  setNegotiationProgress,
  timeline,
  setTimeline,
  barHeights,
  thoughts,
  setThoughts,
  transcripts,
  setTranscripts,
  hasMounted,
  onStartNegotiation,
  callStatus,
}: {
  persona: string | null;
  negotiationProgress: number;
  setNegotiationProgress: React.Dispatch<React.SetStateAction<number>>;
  timeline: Timeline;
  setTimeline: React.Dispatch<React.SetStateAction<Timeline>>;
  barHeights: { orange: number[]; blue: number[] };
  thoughts: Thought[];
  setThoughts: React.Dispatch<React.SetStateAction<Thought[]>>;
  transcripts: Array<{ id: string; agent: string; text: string }>;
  setTranscripts: React.Dispatch<React.SetStateAction<Array<{ id: string; agent: string; text: string }>>>;
  hasMounted: boolean;
  onStartNegotiation: () => void;
  callStatus: string;
}) {
  const room = useRoomContext();

  // Track-based role mapping for agents (LiveKit agent IDs are randomized)
  const tracks = useTracks(
    [{ source: Track.Source.Microphone, withPlaceholder: false }],
    { onlySubscribed: true }
  );

  const agentTracks = tracks.filter(t => t.participant.identity.startsWith("agent-"));

  // Create stable participant ID to agent name mapping using useMemo
  const participantToAgent = React.useMemo(() => {
    const mapping = new Map<string, string>();

    if (agentTracks.length >= 2) {
      // First agent = Halima, Second agent = Alex
      mapping.set(agentTracks[0].participant.identity, "Halima");
      mapping.set(agentTracks[1].participant.identity, "Alex");

      console.log("🗺️ Agent Mapping Created:", {
        halima: agentTracks[0].participant.identity,
        alex: agentTracks[1].participant.identity,
      });
    } else if (agentTracks.length === 1) {
      // If only one agent, assume it's Halima (seller starts first)
      mapping.set(agentTracks[0].participant.identity, "Halima");
      console.log("🗺️ Partial Mapping (Halima only):", agentTracks[0].participant.identity);
    }

    return mapping;
  }, [agentTracks.length, agentTracks[0]?.participant.identity, agentTracks[1]?.participant.identity]);

  useEffect(() => {
    if (!room) return;

    const onDataReceived = (payload: Uint8Array, participant?: Participant) => {
      const raw = new TextDecoder().decode(payload);
      console.warn("📥 RAW DATA RECEIVED:", raw, {
        from: participant?.identity || participant?.sid,
      });

      let data;
      try {
        data = JSON.parse(raw);
        console.warn("✅ PARSED DATA:", data);
      } catch (e) {
        console.error("❌ JSON parse failed:", raw);
        return;
      }

      // For price_update, trust the agent field from backend
      // For other messages, derive from participant
      const agentName = data.type === "price_update"
        ? data.agent
        : (participant?.name || (participant?.identity.includes("halima") || participant?.identity.includes("juma") || participant?.name?.toLowerCase().includes("halima") ? "Halima" : "Alex"));

      if (data.type === "thought") {
        const id = `${agentName}-${crypto.randomUUID()}`;
        setThoughts((prev: Thought[]) => [
          { id, agent: agentName, text: data.text, type: "insight" },
          ...prev.slice(0, 9),
        ]);
      } else if (data.type === "negotiation_timeline") {
        console.warn("🟢 TIMELINE EVENT HIT", data);
        console.warn("🟢 SETTING TIMELINE STATE", {
          before: timeline,
          incoming: data,
        });

        setTimeline({
          turn: data.turn,
          round: data.round,
          maxRounds: data.max_rounds,
        });
        // Also sync global progress for the waveform
        const progress = (data.round / data.max_rounds) * 100;
        setNegotiationProgress(progress);
      } else if (data.type === "SPEECH" || data.type === "HALIMA_DONE" || data.type === "ALEX_SPEECH") {
        if (data.text) {
          const speaker = data.speaker || agentName;
          setTranscripts(prev => {
            const next = [
              ...prev,
              { id: crypto.randomUUID(), agent: speaker, text: data.text }
            ];
            return next.length > 50 ? next.slice(-50) : next;
          });
        }
      }
    };

    const handleTranscription = (
      segments: TranscriptionSegment[],
      participant?: Participant
    ) => {
      if (!participant) return;

      // Use participant mapping to determine agent name
      const agentName = participantToAgent.get(participant.identity) || "Alex";

      console.log("📝 Transcript from:", {
        identity: participant.identity,
        name: participant.name,
        mappedTo: agentName,
        mapSize: participantToAgent.size,
      });

      const finalSegments = segments.filter(s => s.final && s.text.trim());

      if (finalSegments.length === 0) return;

      setTranscripts(prev => {
        const next = [
          ...prev,
          ...finalSegments.map(segment => ({
            id: crypto.randomUUID(),
            agent: agentName,
            text: segment.text,
          })),
        ];

        // ✅ cap buffer to last 50 entries
        return next.length > 50 ? next.slice(-50) : next;
      });
    };

    room.on(RoomEvent.Connected, () => {
      console.warn("✅ ROOM CONNECTED");
    });

    room.on(RoomEvent.DataReceived, onDataReceived);
    room.on(RoomEvent.TranscriptionReceived, handleTranscription);

    return () => {
      room.off(RoomEvent.DataReceived, onDataReceived);
      room.off(RoomEvent.TranscriptionReceived, handleTranscription);
    };
  }, [room, setThoughts, setTranscripts, setTimeline, setNegotiationProgress, timeline, participantToAgent]);

  useEffect(() => {
    console.warn("🔁 TIMELINE STATE CHANGED:", timeline);
  }, [timeline]);

  const halimaTrack = agentTracks[0];
  const alexTrack = agentTracks[1];

  const halimaOnline = Boolean(halimaTrack);
  const alexOnline = Boolean(alexTrack);

  const halimaSpeaking = Boolean(halimaTrack?.participant.isSpeaking);
  const alexSpeaking = Boolean(alexTrack?.participant.isSpeaking);

  return (
    <div className="max-w-7xl mx-auto">
      {/* Call Status Toast */}
      {callStatus && (
        <div className="fixed top-4 right-4 bg-orange-500 text-black px-6 py-3 rounded-lg font-bold shadow-lg z-50 animate-pulse">
          {callStatus}
        </div>
      )}

      <div className="mb-4 p-3 bg-red-900/80 text-white font-mono text-[10px] border border-red-500 rounded flex justify-between items-center backdrop-blur-sm">
      </div>
      <div className="flex justify-between items-center mb-12 border-b border-white/5 pb-8">
        <div>
          {/* Persona Header */}
          <div className="flex items-center gap-3 mb-2">
            <div className={`w-3 h-3 rounded-full ${persona === "Halima" ? "bg-orange-500" : "bg-blue-500"} animate-pulse`}></div>
            <h1 className="text-3xl font-black tracking-tighter uppercase italic">
              <span className={persona === "Halima" ? "text-orange-500" : "text-blue-500"}>
                {persona}'s Agent
              </span>
            </h1>
          </div>
          <p className="text-gray-500 text-xs mt-1 uppercase tracking-widest">Farmer&apos;s Strategic Negotiation Hub</p>
        </div>
        <div className="flex gap-6 items-center">
          <button
            onClick={onStartNegotiation}
            className="px-6 py-3 bg-orange-500 hover:bg-orange-600 border border-orange-400 rounded-xl text-xs text-black font-black uppercase tracking-widest transition-all hover:scale-105 active:scale-95 shadow-[0_0_20px_rgba(249,115,22,0.3)]"
          >
            Start Negotiation
          </button>
          <div className="flex gap-4 pr-6 border-r border-white/10 items-center">
            <div className="flex items-center gap-2">
              <div
                className={`w-3 h-3 rounded-full transition-all duration-300 ${halimaOnline
                  ? `bg-green-500 shadow-[0_0_12px_rgba(34,197,94,0.6)] ${halimaSpeaking ? "animate-pulse scale-150" : "animate-ping"}`
                  : "bg-red-500"
                  }`}
              />
              <span className={`text-[10px] font-mono uppercase transition-colors ${halimaOnline ? "text-gray-300" : "text-gray-600"}`}>
                HALIMA: {halimaOnline ? (halimaSpeaking ? "SPEAKING" : "ACTIVE") : "OFFLINE"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div
                className={`w-3 h-3 rounded-full transition-all duration-300 ${alexOnline
                  ? `bg-green-500 shadow-[0_0_12px_rgba(34,197,94,0.6)] ${alexSpeaking ? "animate-pulse scale-150" : "animate-ping"}`
                  : "bg-red-500"
                  }`}
              />
              <span className={`text-[10px] font-mono uppercase transition-colors ${alexOnline ? "text-gray-300" : "text-gray-600"}`}>
                ALEX: {alexOnline ? (alexSpeaking ? "SPEAKING" : "ACTIVE") : "OFFLINE"}
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
        {/* Transcript - Left Side */}
        <div className="col-span-6">
          <div className="h-[calc(100vh-280px)]">
            <Transcript transcripts={transcripts} />
          </div>
        </div>

        {/* Waveform - Right Side */}
        <div className="col-span-6">
          <div className="h-[calc(100vh-280px)] rounded-xl bg-gradient-to-t from-orange-500/5 to-transparent border border-white/5 flex flex-col items-center justify-center relative overflow-hidden">
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
                  className={`w-1 bg-orange-500/40 rounded-full ${halimaSpeaking ? "animate-bounce" : ""}`}
                  style={{
                    height: `${halimaSpeaking ? height : 10}%`,
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

                {/* Round Progress Indicator */}
                <div className="mt-6 flex flex-col items-center">
                  <p className="text-[8px] font-mono text-gray-600 uppercase mb-2">Round Progress</p>
                  <div className="flex gap-1.5">
                    {Array.from({ length: timeline.maxRounds }).map((_, i) => (
                      <div
                        key={i}
                        className={`w-2 h-2 rounded-full transition-all duration-500 ${i < timeline.round ? "bg-orange-500 shadow-[0_0_8px_rgba(249,115,22,0.4)]" : "bg-white/10"
                          }`}
                      />
                    ))}
                  </div>
                  <p className="mt-2 text-xs font-black tracking-tighter text-white">
                    {timeline.round} <span className="text-[10px] text-gray-600">/ {timeline.maxRounds}</span>
                  </p>
                </div>
              </div>
              {hasMounted && barHeights.blue.map((height: number, i: number) => (
                <div
                  key={i}
                  className={`w-1 bg-blue-500/40 rounded-full ${alexSpeaking ? "animate-bounce" : ""}`}
                  style={{
                    height: `${alexSpeaking ? height : 10}%`,
                    animationDelay: `${i * 0.1}s`,
                    transition: "height 0.2s ease"
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
