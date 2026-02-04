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

  // Call signaling states
  const [callState, setCallState] = useState<"idle" | "calling" | "ringing" | "connected">("idle");
  const [incomingCallFrom, setIncomingCallFrom] = useState<string | null>(null);
  const [outgoingCallTo, setOutgoingCallTo] = useState<string | null>(null);

  // Cross-persona online status
  const [halimaOnlineState, setHalimaOnlineState] = useState(false);
  const [alexOnlineState, setAlexOnlineState] = useState(false);


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

  // Poll persona status
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const hResp = await fetch("http://localhost:8000/persona/status/Halima");
        if (hResp.ok) {
          const hData = await hResp.json();
          setHalimaOnlineState(hData.status === "online");
        }

        const aResp = await fetch("http://localhost:8000/persona/status/Alex");
        if (aResp.ok) {
          const aData = await aResp.json();
          setAlexOnlineState(aData.status === "online");
        }
      } catch (e) {
        // Only log error once to prevent console spam
        console.warn("Backend status check failed - ensure main.py is running on :8000");
      }
    };

    const interval = setInterval(checkStatus, 3000);
    checkStatus();
    return () => clearInterval(interval);
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

  // Call signaling functions
  const initiateCall = async () => {
    const toPersona = persona === "Halima" ? "Alex" : "Halima";
    console.log(`📞 [UI] Initiating call to ${toPersona}...`);
    setCallState("calling");
    setOutgoingCallTo(toPersona);

    try {
      const resp = await fetch(`http://localhost:8000/call/offer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          from_persona: persona,
          to_persona: toPersona
        })
      });

      if (!resp.ok) {
        throw new Error(`Server error: ${resp.status}`);
      }

      const data = await resp.json();
      console.log(`📡 [API] /call/offer response:`, data);

      if (data.status === "offline") {
        console.warn(`🛑 [UI] Target ${toPersona} is offline.`);
        setCallStatus(`${toPersona} is offline`);
        setCallState("idle");
        setOutgoingCallTo(null);
        setTimeout(() => setCallStatus(""), 3000);
      } else {
        console.log(`✅ [UI] Call offer sent successfully.`);
      }
    } catch (e) {
      console.error("❌ [API] Failed to send call offer:", e);
      setCallState("idle");
      setOutgoingCallTo(null);
    }
  };

  const acceptCall = async () => {
    if (!incomingCallFrom) return;
    const meetingId = `harvest_deal_${Math.floor(Math.random() * 10000)}`;
    console.log(`✅ [UI] Accepting call from ${incomingCallFrom}. Generated MeetingID: ${meetingId}`);

    try {
      const resp = await fetch(`http://localhost:8000/call/accept`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          from_persona: incomingCallFrom,
          to_persona: persona,
          meeting_id: meetingId
        })
      });
      const data = await resp.json();
      console.log(`📡 [API] /call/accept response:`, data);

      setCallState("connected");
      setNegotiationProgress(10);
      setLkToken(null);
      setTimeout(() => {
        fetch(`http://localhost:8000/livekit/token?participant_name=User_${persona}&persona=${persona}&room_name=${data.room}`)
          .then(res => res.json())
          .then(d => {
            console.log(`🔑 [UI] Joined shared call room.`);
            setLkToken(d.token);
          });
      }, 500);
    } catch (e) {
      console.error("❌ [API] Failed to accept call:", e);
    }
  };

  const declineCall = async () => {
    if (!incomingCallFrom) return;
    console.warn(`✖️ [UI] Declining call from ${incomingCallFrom}...`);

    try {
      await fetch(`http://localhost:8000/call/decline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          from_persona: incomingCallFrom,
          to_persona: persona
        })
      });
      console.log(`✅ [UI] Call declined successfully.`);
    } catch (e) {
      console.error("❌ [API] Failed to decline call:", e);
    }
    setCallState("idle");
    setIncomingCallFrom(null);
  };


  if (!hasMounted) return null;

  return (
    <main className="min-h-screen bg-[#050505] text-white p-8 font-sans selection:bg-orange-500/30 relative overflow-hidden">
      {/* Animated Background Orbs */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute top-20 left-20 w-96 h-96 bg-orange-500/20 rounded-full blur-3xl animate-float animate-glow-pulse" />
        <div className="absolute bottom-20 right-20 w-80 h-80 bg-blue-500/20 rounded-full blur-3xl animate-float animate-glow-pulse" style={{ animationDelay: '2s' }} />
        <div className="absolute top-1/2 left-1/2 w-64 h-64 bg-purple-500/10 rounded-full blur-3xl animate-float animate-glow-pulse" style={{ animationDelay: '4s' }} />
      </div>

      {!inRoom ? (
        <div className="max-w-7xl mx-auto h-[80vh] flex flex-col items-center justify-center text-center relative z-10">
          {/* Logo/Title */}
          <div className="mb-12 opacity-0 animate-fade-in-up">
            <h1 className="text-7xl font-black tracking-tighter uppercase italic mb-4 bg-gradient-to-r from-orange-500 via-orange-400 to-orange-600 bg-clip-text text-transparent animate-gradient-shift">
              Harvest
            </h1>
            <div className="flex items-center justify-center gap-2 mb-6">
              <div className="h-px w-12 bg-gradient-to-r from-transparent to-orange-500/50" />
              <p className="text-xs uppercase tracking-[0.3em] text-orange-500/60 font-bold">AI-Powered Negotiation</p>
              <div className="h-px w-12 bg-gradient-to-l from-transparent to-orange-500/50" />
            </div>
            <p className="text-gray-400 text-sm max-w-md mx-auto leading-relaxed">
              Stateless decentralized agents for agricultural negotiation. Select your role to begin.
            </p>
          </div>

          {/* Meeting ID Input */}
          <div className="mb-16 w-full max-w-sm mx-auto opacity-0 animate-fade-in-up stagger-2">
            <label className="block text-[10px] uppercase font-black tracking-widest text-gray-500 mb-3 flex items-center justify-center gap-2">
              <span className="w-1 h-1 rounded-full bg-orange-500 animate-pulse" />
              Meeting ID
              <span className="w-1 h-1 rounded-full bg-orange-500 animate-pulse" />
            </label>
            <div className="relative group">
              <input
                type="text"
                value={meetingId}
                onChange={(e) => setMeetingId(e.target.value)}
                className="w-full bg-white/5 border border-white/10 rounded-2xl px-6 py-4 text-center font-mono text-orange-500 focus:outline-none focus:border-orange-500/50 focus:bg-white/10 transition-all duration-300 backdrop-blur-sm group-hover:border-orange-500/30"
                placeholder="e.g. HARVEST_DEAL_2026"
              />
              <div className="absolute inset-0 rounded-2xl bg-gradient-to-r from-orange-500/0 via-orange-500/5 to-orange-500/0 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
            </div>
          </div>

          {/* Persona Selection */}
          <div className="flex gap-8 opacity-0 animate-fade-in-scale stagger-3">
            {/* Halima Button */}
            <button
              onClick={() => enterPresenceRoom("Halima")}
              className="group relative px-16 py-8 bg-gradient-to-br from-orange-500 to-orange-600 rounded-3xl hover:scale-105 active:scale-95 transition-all duration-300 text-left overflow-hidden shadow-2xl shadow-orange-500/20"
            >
              {/* Shimmer Effect */}
              <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-1000" />

              {/* Content */}
              <div className="relative z-10">
                <span className="block text-black font-black uppercase tracking-widest text-2xl mb-2">Halima</span>
                <span className="block text-black/70 text-xs font-bold uppercase tracking-tight">The Seller • Farmer</span>
              </div>

              {/* Glow */}
              <div className="absolute -inset-3 bg-orange-500/30 blur-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-500 -z-10" />
            </button>

            {/* Alex Button */}
            <button
              onClick={() => enterPresenceRoom("Alex")}
              className="group relative px-16 py-8 bg-white/5 backdrop-blur-md border-2 border-white/10 rounded-3xl hover:scale-105 active:scale-95 transition-all duration-300 text-left overflow-hidden shadow-2xl hover:border-blue-500/30"
            >
              {/* Shimmer Effect */}
              <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-1000" />

              {/* Content */}
              <div className="relative z-10">
                <span className="block text-white font-black uppercase tracking-widest text-2xl mb-2">Alex</span>
                <span className="block text-white/50 text-xs font-bold uppercase tracking-tight">The Buyer • Commodity Agent</span>
              </div>

              {/* Glow */}
              <div className="absolute -inset-3 bg-blue-500/20 blur-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-500 -z-10" />
            </button>
          </div>

          {/* Decorative Elements */}
          <div className="absolute bottom-8 left-1/2 -translate-x-1/2 flex gap-2 opacity-0 animate-fade-in-up stagger-4">
            {[...Array(3)].map((_, i) => (
              <div
                key={i}
                className="w-1.5 h-1.5 rounded-full bg-orange-500/30"
                style={{ animationDelay: `${i * 0.2}s` }}
              />
            ))}
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
            persona={persona!}
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
            setCallStatus={setCallStatus}
            callState={callState}
            setCallState={setCallState}
            incomingCallFrom={incomingCallFrom}
            setIncomingCallFrom={setIncomingCallFrom}
            outgoingCallTo={outgoingCallTo}
            setOutgoingCallTo={setOutgoingCallTo}
            initiateCall={initiateCall}
            acceptCall={acceptCall}
            declineCall={declineCall}
            halimaOnlineState={halimaOnlineState}
            alexOnlineState={alexOnlineState}
            setLkToken={setLkToken}
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
  setCallStatus,
  callState,
  setCallState,
  incomingCallFrom,
  setIncomingCallFrom,
  outgoingCallTo,
  setOutgoingCallTo,
  initiateCall,
  acceptCall,
  declineCall,
  halimaOnlineState,
  alexOnlineState,
  setLkToken,
}: {
  persona: string;
  negotiationProgress: number;
  setNegotiationProgress: (p: number) => void;
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
  setCallStatus: (s: string) => void;
  callState: "idle" | "calling" | "ringing" | "connected";
  setCallState: (s: "idle" | "calling" | "ringing" | "connected") => void;
  incomingCallFrom: string | null;
  setIncomingCallFrom: (p: string | null) => void;
  outgoingCallTo: string | null;
  setOutgoingCallTo: (p: string | null) => void;
  initiateCall: () => void;
  acceptCall: () => void;
  declineCall: () => void;
  halimaOnlineState: boolean;
  alexOnlineState: boolean;
  setLkToken: (token: string | null) => void;
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
      console.log(`📩 [SIGNAL] Data received in room ${room.name}:`, raw, {
        from: participant?.identity || participant?.sid,
      });

      let data: any;
      try {
        data = JSON.parse(raw);
        console.warn("✅ PARSED DATA:", data);

        if (data.type === "CALL_OFFER") {
          console.log(`🔔 [SIGNAL] Incoming call from ${data.from}`);
          setIncomingCallFrom(data.from);
          setCallState("ringing");
          return;
        } else if (data.type === "CALL_ACCEPTED") {
          console.log(`🤝 [SIGNAL] Call accepted by ${data.by}. Joining room: ${data.room}`);
          setCallState("connected");
          setNegotiationProgress(10);

          // Disconnect from current room first
          console.log(`🔌 [SIGNAL] Disconnecting from ${room.name} to switch to call room...`);
          room.disconnect();

          // Wait for disconnect, then join call room
          setTimeout(() => {
            fetch(`http://localhost:8000/livekit/token?participant_name=User_${persona}&persona=${persona}&room_name=${data.room}`)
              .then(res => res.json())
              .then(d => {
                console.log(`🔑 [SIGNAL] Joined call room ${data.room} with new token.`);
                setLkToken(d.token);
              });
          }, 1000);
          return;
        } else if (data.type === "CALL_DECLINED") {
          console.warn(`✖️ [SIGNAL] Call declined by ${data.by}`);
          setCallStatus(`${data.by} declined the call`);
          setCallState("idle");
          setOutgoingCallTo(null);
          setTimeout(() => setCallStatus(""), 3000);
          return;
        }
      } catch (e) {
        console.error("❌ [SIGNAL] Failed to parse data message:", e);
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

  const halimaTrack = agentTracks.find(t => t.participant.identity.includes("halima") || (t.participant.name?.toLowerCase().includes("halima")));
  const alexTrack = agentTracks.find(t => t.participant.identity.includes("alex") || (t.participant.name?.toLowerCase().includes("alex")));

  const halimaOnline = halimaOnlineState || Boolean(halimaTrack) || callState === "connected";
  const alexOnline = alexOnlineState || Boolean(alexTrack) || callState === "connected";

  const halimaSpeaking = Boolean(halimaTrack?.participant.isSpeaking);
  const alexSpeaking = Boolean(alexTrack?.participant.isSpeaking);

  const isHalimaUser = persona === "Halima";
  const isAlexUser = persona === "Alex";

  const halimaStatus = halimaTrack
    ? (halimaSpeaking ? "SPEAKING" : "ACTIVE")
    : (callState === "connected" ? "CONNECTED" : (halimaOnlineState ? "READY" : "OFFLINE"));
  const alexStatus = alexTrack
    ? (alexSpeaking ? "SPEAKING" : "ACTIVE")
    : (callState === "connected" ? "CONNECTED" : (alexOnlineState ? "READY" : "OFFLINE"));

  return (
    <div className="max-w-7xl mx-auto">
      {/* Incoming Call Modal (Ringing) */}
      {callState === "ringing" && incomingCallFrom && (
        <div className="fixed inset-0 bg-black/90 flex items-center justify-center z-50 backdrop-blur-sm">
          <div className="bg-gray-900 p-10 rounded-2xl text-center border border-orange-500/30 shadow-2xl max-w-md">
            <div className="w-24 h-24 bg-orange-500 rounded-full mx-auto mb-6 animate-pulse flex items-center justify-center">
              <span className="text-4xl">📞</span>
            </div>
            <h2 className="text-3xl font-black mb-3 text-orange-500">Incoming Call</h2>
            <p className="text-gray-300 mb-8 text-lg">{incomingCallFrom} is calling...</p>

            <div className="flex gap-4 justify-center">
              <button
                onClick={declineCall}
                className="px-8 py-4 bg-red-500 hover:bg-red-600 rounded-xl font-bold text-lg transition-all hover:scale-105"
              >
                Decline
              </button>
              <button
                onClick={acceptCall}
                className="px-8 py-4 bg-green-500 hover:bg-green-600 rounded-xl font-bold text-lg transition-all hover:scale-105"
              >
                Accept
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Outgoing Call Modal (Calling) */}
      {callState === "calling" && outgoingCallTo && (
        <div className="fixed inset-0 bg-black/90 flex items-center justify-center z-50 backdrop-blur-sm">
          <div className="bg-gray-900 p-10 rounded-2xl text-center border border-blue-500/30 shadow-2xl max-w-md">
            <div className="w-24 h-24 bg-blue-500 rounded-full mx-auto mb-6 animate-ping flex items-center justify-center">
              <span className="text-4xl">📱</span>
            </div>
            <h2 className="text-3xl font-black mb-3 text-blue-500">Calling...</h2>
            <p className="text-gray-300 mb-8 text-lg">{outgoingCallTo}</p>

            <button
              onClick={() => {
                setCallState("idle");
                setOutgoingCallTo(null);
              }}
              className="px-8 py-4 bg-red-500 hover:bg-red-600 rounded-xl font-bold text-lg transition-all hover:scale-105"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Call Status Toast */}
      {callStatus && (
        <div className="fixed top-4 right-4 bg-orange-500 text-black px-6 py-3 rounded-lg font-bold shadow-lg z-50 animate-pulse">
          {callStatus}
        </div>
      )}

      <div className="flex justify-between items-center mb-12 border-b border-white/5 pb-8">
        {/* Persona Header */}
        <div>
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

        {/* Call UI Block */}
        <div className="relative p-4 rounded-2xl border border-white/10 shadow-2xl backdrop-blur-md bg-white/5 flex items-center gap-6">
          <button
            onClick={initiateCall}
            disabled={callState !== "idle"}
            className={`relative px-6 py-3 rounded-xl text-xs text-white font-black uppercase tracking-widest overflow-hidden transition-all duration-300
                        ${callState === "idle"
                ? "bg-gradient-to-r from-orange-500 to-red-500 hover:from-orange-600 hover:to-red-600 shadow-[0_0_20px_rgba(249,115,22,0.3)] active:scale-95"
                : "bg-gray-700 cursor-not-allowed opacity-70"
              }`}
          >
            <span className="relative z-10">📞 Call {persona === "Halima" ? "Alex" : "Halima"}</span>
            {callState === "idle" && (
              <span className="absolute inset-0 w-full h-full bg-gradient-to-r from-orange-500 to-red-500 opacity-0 hover:opacity-100 transition-opacity duration-300"></span>
            )}
          </button>

          {/* Status Dots */}
          <div className="flex gap-4 items-center pr-6 border-r border-white/10">
            <div className="flex items-center gap-2">
              <div
                className={`w-3 h-3 rounded-full transition-all duration-300 ${halimaOnline ? "bg-green-500 shadow-[0_0_12px_rgba(34,197,94,0.6)]" + (halimaSpeaking ? " animate-pulse scale-150" : " animate-ping") : "bg-red-500"}`}
              />
              <span className={`text-[10px] font-mono uppercase transition-colors ${halimaOnline ? "text-gray-300" : "text-gray-600"}`}>HALIMA: {halimaStatus}</span>
            </div>
            <div className="flex items-center gap-2">
              <div
                className={`w-3 h-3 rounded-full transition-all duration-300 ${alexOnline ? "bg-green-500 shadow-[0_0_12px_rgba(34,197,94,0.6)]" + (alexSpeaking ? " animate-pulse scale-150" : " animate-ping") : "bg-red-500"}`}
              />
              <span className={`text-[10px] font-mono uppercase transition-colors ${alexOnline ? "text-gray-300" : "text-gray-600"}`}>ALEX: {alexStatus}</span>
            </div>
          </div>

          <div className="text-right">
            <p className="text-[10px] text-gray-600 uppercase">LiveKit Room</p>
            <p className="text-xs font-mono text-green-500 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" /> {room.name || "CONNECTING..."}
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
