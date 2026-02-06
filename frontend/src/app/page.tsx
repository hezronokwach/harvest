"use client";

import React, { useState, useEffect } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useTracks,
  useRoomContext,
} from "@livekit/components-react";
import { Participant, Track, RoomEvent, TranscriptionSegment, TrackPublication, RemoteParticipant } from "livekit-client";
import Transcript from "@/components/Transcript";
interface Contract {
  id: string;
  title: string;
  agent: string;
  status: "drafting" | "pending_approval" | "sent" | "rejected";
  contract_data: {
    buyer: string;
    product: string;
    price: string;
    quantity: string;
    delivery: string;
    payment: string;
  };
}

interface SharedFile {
  name: string;
  url: string;
  from: string;
  timestamp: string;
  contract_data: {
    buyer: string;
    product: string;
    price: string;
    quantity: string;
    delivery: string;
    payment: string;
  };
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




  const resetNegotiationState = () => {
    setThoughts([]);
    setTranscripts([]);
  };
  const [barHeights, setBarHeights] = useState({ orange: [0, 0, 0], blue: [0, 0, 0] });
  const [thoughts, setThoughts] = useState<Array<{ id: string; agent: string; text: string; type: "strategy" | "insight" | "warning" }>>([]);
  const [transcripts, setTranscripts] = useState<Array<{ id: string; agent: string; text: string }>>([]);
  const [callStatus, setCallStatus] = useState<string>("");

  // Call signaling states
  const [callState, setCallState] = useState<"idle" | "calling" | "ringing" | "connected">("idle");
  const [incomingCallFrom, setIncomingCallFrom] = useState<string | null>(null);
  const [outgoingCallTo, setOutgoingCallTo] = useState<string | null>(null);
  // Contract states
  const [contractStatus, setContractStatus] = useState<"none" | "drafting" | "pending_approval" | "sent" | "received">("none");
  const [pendingContract, setPendingContract] = useState<Contract | null>(null);
  const [sharedFiles, setSharedFiles] = useState<SharedFile[]>([]);



  // Cross-persona online status
  const [halimaOnlineState, setHalimaOnlineState] = useState(false);
  const [alexOnlineState, setAlexOnlineState] = useState(false);


  useEffect(() => {
    const timer = setTimeout(() => setHasMounted(true), 0);
    return () => clearTimeout(timer);
  }, []);

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
      resetNegotiationState();

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
      resetNegotiationState();
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
          key={lkToken}
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
            resetNegotiationState={resetNegotiationState}
            contractStatus={contractStatus}
            setContractStatus={setContractStatus}
            pendingContract={pendingContract}
            setPendingContract={setPendingContract}
            sharedFiles={sharedFiles}
            setSharedFiles={setSharedFiles}
          />
          <RoomAudioRenderer />
        </LiveKitRoom>
      )}
    </main>
  );
}

function DashboardContent({
  persona,
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
  resetNegotiationState,
  contractStatus,
  setContractStatus,
  pendingContract,
  setPendingContract,
  sharedFiles,
  setSharedFiles,
}: {
  persona: string;
  barHeights: { orange: number[]; blue: number[] };
  thoughts: Thought[];
  setThoughts: React.Dispatch<React.SetStateAction<Thought[]>>;
  transcripts: { id: string; agent: string; text: string }[];
  setTranscripts: React.Dispatch<React.SetStateAction<{ id: string; agent: string; text: string }[]>>;
  hasMounted: boolean;
  onStartNegotiation: () => void;
  callStatus: string;
  setCallStatus: React.Dispatch<React.SetStateAction<string>>;
  callState: "idle" | "calling" | "ringing" | "connected";
  setCallState: React.Dispatch<React.SetStateAction<"idle" | "calling" | "ringing" | "connected">>;
  incomingCallFrom: string | null;
  setIncomingCallFrom: React.Dispatch<React.SetStateAction<string | null>>;
  outgoingCallTo: string | null;
  setOutgoingCallTo: React.Dispatch<React.SetStateAction<string | null>>;
  initiateCall: () => void;
  acceptCall: () => void;
  declineCall: () => void;
  halimaOnlineState: boolean;
  alexOnlineState: boolean;
  setLkToken: (t: string | null) => void;
  resetNegotiationState: () => void;
  contractStatus: "none" | "drafting" | "pending_approval" | "sent" | "received";
  setContractStatus: React.Dispatch<React.SetStateAction<"none" | "drafting" | "pending_approval" | "sent" | "received">>;
  pendingContract: Contract | null;
  setPendingContract: React.Dispatch<React.SetStateAction<Contract | null>>;
  sharedFiles: SharedFile[];
  setSharedFiles: React.Dispatch<React.SetStateAction<SharedFile[]>>;
}) {
  const room = useRoomContext();

  // Sync-capable state for waveforms (driven by DataPackets + LiveKit VAD)
  const [halimaIsSpeaking, setHalimaIsSpeaking] = useState(false);
  const [alexIsSpeaking, setAlexIsSpeaking] = useState(false);

  // Stable ID and Content-based Deduplication Buffer
  const lastTranscriptHashes = React.useRef<Set<string>>(new Set());
  const segmentToId = React.useRef<Map<string, string>>(new Map());

  // Track-based role mapping for agents (LiveKit agent IDs are randomized)
  const tracks = useTracks(
    [{ source: Track.Source.Microphone, withPlaceholder: false }],
    { onlySubscribed: true }
  );

  const agentTracks = React.useMemo(() =>
    tracks.filter(t => {
      const id = t.participant.identity.toLowerCase();
      // Broad check for typical agent identity patterns in LiveKit
      return id.startsWith("agent-") || id.includes("-worker") || id.startsWith("aj_");
    }),
    [tracks]
  );

  // Create stable participant ID to agent name mapping using metadata or identity
  const participantToAgent = React.useMemo(() => {
    const mapping = new Map<string, string>();

    agentTracks.forEach(track => {
      const p = track.participant;
      let name = "";

      // 1. Try metadata (Persona is set in main.py dispatch)
      try {
        if (p.metadata) {
          const meta = JSON.parse(p.metadata);
          if (meta.persona) name = meta.persona;
        }
      } catch (e) { }

      // 2. Fallback to identity string search
      if (!name) {
        const id = p.identity.toLowerCase();
        if (id.includes("halima") || id.includes("juma")) name = "Halima";
        else if (id.includes("alex")) name = "Alex";
      }

      if (name) {
        mapping.set(p.identity, name);
      }
    });


    return mapping;
  }, [agentTracks]);

  // Track room name to ensure we only clear transcripts on meaningful room switches
  const [lastRoomName, setLastRoomName] = useState("");

  useEffect(() => {
    if (!room) return;
    let syncTimeout: NodeJS.Timeout;

    const sendSyncRequest = () => {
      clearTimeout(syncTimeout);
      syncTimeout = setTimeout(() => {
        if (room.state !== "connected") return;
        const lp = room.localParticipant;
        if (!lp) return;

        console.warn("📤 Sending SYNC_REQUEST to Agents (Delayed)...");
        try {
          lp.publishData(
            new TextEncoder().encode(JSON.stringify({ type: "SYNC_REQUEST" })),
            { reliable: true }
          );
        } catch (e) {
          console.error("Failed to send SYNC_REQUEST:", e);
        }
      }, 1500);
    };

    if (room.state === "connected" && lastRoomName !== room.name) {
      console.warn(`🎯 NEW ROOM CONNECTED: ${room.name}. Resetting state.`);
      setTranscripts([]);
      setLastRoomName(room.name);
      sendSyncRequest();
    }

    const getAgentFromIdentity = (id?: string, name?: string) => {
      const lowerId = id?.toLowerCase() || "";
      const lowerName = name?.toLowerCase() || "";
      if (lowerId.includes("halima") || lowerId.includes("seller") || lowerName.includes("halima")) return "Halima";
      if (lowerId.includes("alex") || lowerId.includes("buyer") || lowerName.includes("alex")) return "Alex";
      return "Agent";
    };

    const onDataReceived = (payload: Uint8Array, participant?: Participant) => {
      const raw = new TextDecoder().decode(payload);
      try {
        const data = JSON.parse(raw);
        console.warn("📦 DATA PACKET:", data);

        if (data.type === "CALL_OFFER") {
          setIncomingCallFrom(data.from);
          setCallState("ringing");
          return;
        } else if (data.type === "CALL_ACCEPTED") {
          setCallState("connected");
          resetNegotiationState();
          room.disconnect();
          setTimeout(() => {
            fetch(`http://localhost:8000/livekit/token?participant_name=User_${persona}&persona=${persona}&room_name=${data.room}`)
              .then(res => res.json())
              .then(d => {
                setLkToken(d.token);
              });
          }, 1000);
          return;
        } else if (data.type === "CALL_DECLINED") {
          setCallStatus(`${data.by} declined the call`);
          setCallState("idle");
          setOutgoingCallTo(null);
          setTimeout(() => setCallStatus(""), 3000);
          return;
        } else if (data.type === "CONTRACT_INTENT") {
          console.log("📝 Received CONTRACT_INTENT:", data);
          if (data.agent === "Halima") setContractStatus("drafting");
          return;
        } else if (data.type === "CONTRACT_PREVIEW") {
          console.log("📝 Received CONTRACT_PREVIEW:", data);
          setPendingContract({
            id: data.contract_id,
            title: data.title,
            agent: data.agent,
            status: "pending_approval",
            contract_data: data.contract_data
          });
          setContractStatus("pending_approval");
          return;
        } else if (data.type === "CONTRACT_APPROVED") {
          console.log("✅ Received CONTRACT_APPROVED:", data);
          setContractStatus("sent");
          setPendingContract(null);
          return;
        } else if (data.type === "CONTRACT_REJECTED") {
          console.log("❌ Received CONTRACT_REJECTED:", data);
          setContractStatus("none");
          setPendingContract(null);
          return;
        } else if (data.type === "FILE_SHARED") {
          console.log("📥 Received FILE_SHARED:", data);
          setSharedFiles(prev => [...prev, {
            name: data.filename,
            url: data.url,
            from: data.from,
            timestamp: new Date().toLocaleTimeString(),
            contract_data: data.contract_data
          }]);
          setContractStatus("received");
          return;
        }

        // --- NEGOTIATION SYNC LOGIC ---
        const agentName = (data.type === "offer_update" || data.type === "DEAL_FINALIZED" || data.type === "price_update" || data.type === "thought")
          ? data.agent
          : (participantToAgent.get(participant?.identity || "") || getAgentFromIdentity(participant?.identity, participant?.name));

        if (data.type === "thought") {
          // Insights: Display agent's tactical thoughts in the right panel
          console.log("💡 Processing Tactical Thought:", data);
          const id = `${data.agent}-${crypto.randomUUID()}`;
          setThoughts((prev: Thought[]) => {
            if (prev.some(t => t.text === data.text)) return prev;
            const newThought: Thought = { id, agent: data.agent || "Agent", text: data.text, type: "insight" };
            const next = [newThought, ...prev];
            return next.slice(0, 10);
          });
        } else if (data.type === "SPEECH") {
          // Final Speech Sync: Handle final transcripts published by agents via DataPackets
          if (data.text && data.is_final !== false) {
            const speaker = data.speaker || agentName || "Agent";
            // Normalize for hashing: trim, lowercase, strip trailing periods/punctuation
            const normalizedText = data.text.trim().toLowerCase().replace(/[.!?]+$/, "");
            const contentHash = `${speaker.toLowerCase()}:${normalizedText}`;

            if (lastTranscriptHashes.current.has(contentHash)) {
              console.warn("🚫 Dropping Duplicate Speech Packet:", contentHash);
              return;
            }

            lastTranscriptHashes.current.add(contentHash);
            setTimeout(() => lastTranscriptHashes.current.delete(contentHash), 5000);

            console.log("📝 Adding Transcript from Packet:", { speaker, text: data.text });
            setTranscripts(prev => {
              if (prev.some(t => t.text === data.text && t.agent === speaker)) return prev;
              const next = [...prev, { id: crypto.randomUUID(), agent: speaker, text: data.text }];
              return next.length > 50 ? next.slice(-50) : next;
            });
          }
        }
        else if (data.type === "SPEECH_STATE") {
          if (data.agent === "Halima") setHalimaIsSpeaking((prev: boolean) => prev === data.is_speaking ? prev : data.is_speaking);
          if (data.agent === "Alex") setAlexIsSpeaking((prev: boolean) => prev === data.is_speaking ? prev : data.is_speaking);
        }
      } catch (e) {
        console.error("❌ [SIGNAL] Data Error:", e);
      }
    };

    const handleTranscription = (segments: TranscriptionSegment[], participant?: Participant) => {
      if (!participant) return;
      const agentName = participantToAgent.get(participant.identity) || getAgentFromIdentity(participant.identity, participant.name);

      // CRITICAL: Ignore transcripts for known agents from audio tracks.
      // We rely EXCLUSIVELY on the "SPEECH" data packet for Halima/Alex transcripts
      // to avoid 3x duplication and fragmentation.
      if (agentName === "Halima" || agentName === "Alex") return;

      const finalSegments = segments.filter(s => s.final && s.text.trim());
      if (finalSegments.length === 0) return;

      setTranscripts(prev => {
        const newTranscripts = finalSegments.map(segment => {
          // Normalize for hashing to catch duplicates across Track/Packet channels
          const normalizedText = segment.text.trim().toLowerCase().replace(/[.!?]+$/, "");
          const contentHash = `${agentName.toLowerCase()}:${normalizedText}`;

          if (lastTranscriptHashes.current.has(contentHash)) {
            console.warn("🚫 Dropping Duplicate Track Transcript:", contentHash);
            return null;
          }
          lastTranscriptHashes.current.add(contentHash);
          setTimeout(() => lastTranscriptHashes.current.delete(contentHash), 5000);

          console.log("🎙️ Adding Transcript from Track:", { agentName, text: segment.text });
          return { id: segment.id || crypto.randomUUID(), agent: agentName, text: segment.text };
        }).filter((t): t is { id: string; agent: string; text: string } => t !== null);

        if (newTranscripts.length === 0) return prev;
        const next = [...prev, ...newTranscripts];
        return next.length > 50 ? next.slice(-50) : next;
      });
    };

    room.on(RoomEvent.Connected, sendSyncRequest);
    room.on(RoomEvent.DataReceived, onDataReceived);
    room.on(RoomEvent.TranscriptionReceived, handleTranscription);

    return () => {
      clearTimeout(syncTimeout);
      room.off(RoomEvent.Connected, sendSyncRequest);
      room.off(RoomEvent.DataReceived, onDataReceived);
      room.off(RoomEvent.TranscriptionReceived, handleTranscription);
    };
  }, [room, setThoughts, setTranscripts, participantToAgent, persona, setLkToken, lastRoomName]);

  const halimaTrack = agentTracks.find(t => participantToAgent.get(t.participant.identity) === "Halima");
  const alexTrack = agentTracks.find(t => participantToAgent.get(t.participant.identity) === "Alex");

  const halimaOnline = halimaOnlineState || Boolean(halimaTrack) || callState === "connected";
  const alexOnline = alexOnlineState || Boolean(alexTrack) || callState === "connected";

  const halimaSpeaking = halimaIsSpeaking || Boolean(halimaTrack?.participant.isSpeaking);
  const alexSpeaking = alexIsSpeaking || Boolean(alexTrack?.participant.isSpeaking);

  const isHalimaUser = persona === "Halima";
  const isAlexUser = persona === "Alex";

  const halimaStatus = halimaTrack
    ? (halimaSpeaking ? "SPEAKING" : "ACTIVE")
    : (callState === "connected" ? "CONNECTED" : (halimaOnlineState ? "READY" : "OFFLINE"));
  const alexStatus = alexTrack
    ? (alexSpeaking ? "SPEAKING" : "ACTIVE")
    : (callState === "connected" ? "CONNECTED" : (alexOnlineState ? "READY" : "OFFLINE"));

  const approveContract = () => {
    if (!room || !pendingContract) return;
    room.localParticipant.publishData(
      new TextEncoder().encode(JSON.stringify({ type: "CONTRACT_APPROVED", contract_id: pendingContract.id })),
      { reliable: true }
    );
    setContractStatus("sent");
    setPendingContract(null);
  };

  const rejectContract = (reason: string) => {
    if (!room || !pendingContract) return;
    room.localParticipant.publishData(
      new TextEncoder().encode(JSON.stringify({ type: "CONTRACT_REJECTED", contract_id: pendingContract.id, reason })),
      { reliable: true }
    );
    setContractStatus("none");
    setPendingContract(null);
  };

  return (
    <div className="max-w-7xl mx-auto">
      {/* Incoming Call Modal (Ringing) */}
      {callState === "ringing" && incomingCallFrom && (
        <div className="fixed inset-0 bg-gradient-to-b from-black/98 via-orange-950/30 to-black/98 flex items-center justify-center z-50 backdrop-blur-2xl">
          {/* Floating Background Orbs */}
          <div className="absolute inset-0 pointer-events-none overflow-hidden">
            <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-orange-500/20 rounded-full blur-3xl animate-float animate-glow-pulse" />
            <div className="absolute bottom-1/4 right-1/4 w-64 h-64 bg-red-500/20 rounded-full blur-3xl animate-float animate-glow-pulse" style={{ animationDelay: '1.5s' }} />
          </div>

          <div className="relative text-center opacity-0 animate-fade-in-scale">
            {/* Premium Icon */}
            <div className="relative w-48 h-48 mx-auto mb-12">
              {/* Outer rotating ring */}
              <div className="absolute inset-0 rounded-full border-2 border-orange-500/20 animate-spin" style={{ animationDuration: '8s' }} />
              <div className="absolute inset-4 rounded-full border-2 border-orange-500/30 animate-spin" style={{ animationDuration: '6s', animationDirection: 'reverse' }} />

              {/* Glow layers */}
              <div className="absolute inset-0 bg-orange-500/10 rounded-full blur-3xl animate-glow-pulse" />
              <div className="absolute inset-8 bg-orange-500/20 rounded-full blur-2xl animate-glow-pulse" style={{ animationDelay: '0.5s' }} />

              {/* Center icon */}
              <div className="absolute inset-12 bg-gradient-to-br from-orange-400 via-orange-500 to-orange-600 rounded-full flex items-center justify-center shadow-2xl shadow-orange-500/50 animate-pulse">
                <svg className="w-16 h-16 text-white" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M20.01 15.38c-1.23 0-2.42-.2-3.53-.56a.977.977 0 00-1.01.24l-1.57 1.97c-2.83-1.35-5.48-3.9-6.89-6.83l1.95-1.66c.27-.28.35-.67.24-1.02-.37-1.11-.56-2.3-.56-3.53 0-.54-.45-.99-.99-.99H4.19C3.65 3 3 3.24 3 3.99 3 13.28 10.73 21 20.01 21c.71 0 .99-.63.99-1.18v-3.45c0-.54-.45-.99-.99-.99z" />
                </svg>
              </div>
            </div>

            {/* Title */}
            <h2 className="text-5xl font-black mb-4 bg-gradient-to-r from-orange-300 via-orange-400 to-orange-600 bg-clip-text text-transparent tracking-tight">
              Incoming Call
            </h2>

            {/* Caller Info */}
            <p className="text-white mb-2 text-2xl font-bold">{incomingCallFrom}</p>
            <p className="text-orange-400/60 mb-16 text-xs uppercase tracking-[0.3em] font-bold">is calling...</p>

            {/* Action Buttons */}
            <div className="flex gap-6 justify-center">
              <button
                onClick={declineCall}
                className="group relative px-12 py-5 bg-red-500/10 border-2 border-red-500/30 rounded-2xl hover:scale-105 active:scale-95 transition-all duration-300 overflow-hidden backdrop-blur-sm hover:border-red-500/60"
              >
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-red-500/20 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-1000" />
                <span className="relative z-10 text-red-400 font-black uppercase tracking-wider">Decline</span>
                <div className="absolute -inset-3 bg-red-500/30 blur-2xl opacity-0 group-hover:opacity-100 transition-opacity -z-10" />
              </button>

              <button
                onClick={acceptCall}
                className="group relative px-12 py-5 bg-gradient-to-br from-green-500 to-emerald-600 rounded-2xl hover:scale-105 active:scale-95 transition-all duration-300 overflow-hidden shadow-2xl shadow-green-500/40"
              >
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-1000" />
                <span className="relative z-10 text-white font-black uppercase tracking-wider">Accept</span>
                <div className="absolute -inset-3 bg-green-500/50 blur-2xl opacity-0 group-hover:opacity-100 transition-opacity -z-10" />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Outgoing Call Modal (Calling) */}
      {callState === "calling" && outgoingCallTo && (
        <div className="fixed inset-0 bg-gradient-to-b from-black/98 via-blue-950/30 to-black/98 flex items-center justify-center z-50 backdrop-blur-2xl">
          {/* Floating Background Orbs */}
          <div className="absolute inset-0 pointer-events-none overflow-hidden">
            <div className="absolute top-1/4 right-1/4 w-96 h-96 bg-blue-500/20 rounded-full blur-3xl animate-float animate-glow-pulse" />
            <div className="absolute bottom-1/4 left-1/4 w-64 h-64 bg-cyan-500/20 rounded-full blur-3xl animate-float animate-glow-pulse" style={{ animationDelay: '1.5s' }} />
          </div>

          <div className="relative text-center opacity-0 animate-fade-in-scale">
            {/* Premium Icon */}
            <div className="relative w-48 h-48 mx-auto mb-12">
              {/* Outer rotating ring */}
              <div className="absolute inset-0 rounded-full border-2 border-blue-500/20 animate-spin" style={{ animationDuration: '8s' }} />
              <div className="absolute inset-4 rounded-full border-2 border-blue-500/30 animate-spin" style={{ animationDuration: '6s', animationDirection: 'reverse' }} />

              {/* Glow layers */}
              <div className="absolute inset-0 bg-blue-500/10 rounded-full blur-3xl animate-glow-pulse" />
              <div className="absolute inset-8 bg-blue-500/20 rounded-full blur-2xl animate-glow-pulse" style={{ animationDelay: '0.5s' }} />

              {/* Center icon with ping animation */}
              <div className="absolute inset-12 bg-gradient-to-br from-blue-400 via-blue-500 to-blue-600 rounded-full flex items-center justify-center shadow-2xl shadow-blue-500/50 animate-pulse">
                <svg className="w-16 h-16 text-white" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M17 1.01L7 1c-1.1 0-2 .9-2 2v18c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V3c0-1.1-.9-1.99-2-1.99zM17 19H7V5h10v14z" />
                </svg>
              </div>
            </div>

            {/* Title */}
            <h2 className="text-5xl font-black mb-4 bg-gradient-to-r from-blue-300 via-blue-400 to-blue-600 bg-clip-text text-transparent tracking-tight">
              Calling...
            </h2>

            {/* Recipient Info */}
            <p className="text-white mb-2 text-2xl font-bold">{outgoingCallTo}</p>
            <p className="text-blue-400/60 mb-16 text-xs uppercase tracking-[0.3em] font-bold">Connecting...</p>

            {/* Cancel Button */}
            <button
              onClick={() => {
                setCallState("idle");
                setOutgoingCallTo(null);
              }}
              className="group relative px-14 py-5 bg-red-500/10 border-2 border-red-500/30 rounded-2xl hover:scale-105 active:scale-95 transition-all duration-300 overflow-hidden backdrop-blur-sm hover:border-red-500/60"
            >
              <div className="absolute inset-0 bg-gradient-to-r from-transparent via-red-500/20 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-1000" />
              <span className="relative z-10 text-red-400 font-black uppercase tracking-wider">Cancel Call</span>
              <div className="absolute -inset-3 bg-red-500/30 blur-2xl opacity-0 group-hover:opacity-100 transition-opacity -z-10" />
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

      {/* Contract Approval Modal (For Halima / Sender) */}
      {contractStatus === "pending_approval" && pendingContract && persona === "Halima" && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-xl flex items-center justify-center z-[60] animate-fade-in">
          <div className="w-full max-w-2xl bg-[#0a0a0a] border-2 border-orange-500/20 rounded-3xl p-8 shadow-2xl relative overflow-hidden group">
            <div className="absolute inset-0 bg-gradient-to-br from-orange-500/5 via-transparent to-transparent opacity-50" />

            {/* Header */}
            <div className="relative mb-8 flex items-center gap-4">
              <div className="w-12 h-12 bg-orange-500/20 rounded-xl flex items-center justify-center">
                <svg className="w-6 h-6 text-orange-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <div>
                <h3 className="text-2xl font-black text-white">{pendingContract.title}</h3>
                <p className="text-orange-400/60 text-xs font-bold uppercase tracking-widest">Drafted by {pendingContract.agent}</p>
              </div>
            </div>

            {/* Content - Structured Grid */}
            <div className="bg-white/5 rounded-2xl p-6 mb-8 border border-white/10 grid grid-cols-2 gap-y-4 gap-x-8">
              {[
                { label: "Buyer", value: pendingContract.contract_data.buyer },
                { label: "Product", value: pendingContract.contract_data.product },
                { label: "Price", value: pendingContract.contract_data.price },
                { label: "Quantity", value: pendingContract.contract_data.quantity },
                { label: "Delivery", value: pendingContract.contract_data.delivery, full: true },
                { label: "Payment", value: pendingContract.contract_data.payment, full: true },
              ].map((field, idx) => (
                <div key={idx} className={field.full ? "col-span-2" : "col-span-1"}>
                  <p className="text-orange-500/40 text-[8px] font-black uppercase tracking-widest mb-1">{field.label}</p>
                  <p className="text-white font-bold">{field.value}</p>
                </div>
              ))}
            </div>

            {/* Actions */}
            <div className="grid grid-cols-2 gap-4">
              <button
                onClick={() => rejectContract("Terms need adjustment.")}
                className="py-4 bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 rounded-xl text-red-500 font-black uppercase tracking-wider transition-all"
              >
                Request Changes
              </button>
              <button
                onClick={approveContract}
                className="py-4 bg-gradient-to-r from-orange-500 to-orange-600 hover:from-orange-400 hover:to-orange-500 rounded-xl text-white font-black uppercase tracking-wider shadow-lg shadow-orange-500/20 transition-all hover:scale-[1.02]"
              >
                Approve & Send
              </button>
            </div>
          </div>
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

        {/* Right Side - Waveform + Bidding */}
        <div className="col-span-6 h-[calc(100vh-280px)] flex flex-col gap-6">
          {/* Waveform - Top Half */}
          <div className="h-2/5 rounded-xl bg-gradient-to-t from-orange-500/5 to-transparent border border-white/5 flex flex-col items-center justify-center relative overflow-hidden">
            <div className="absolute top-4 left-4 flex items-center gap-3">
              <div className="text-[8px] font-bold text-orange-500 uppercase tracking-[0.3em]">Negotiation Waveform</div>
              <div className="flex gap-0.5">
                {[...Array(3)].map((_, i) => (
                  <div key={i} className="w-0.5 h-2 bg-orange-500/20 animate-pulse" style={{ animationDelay: `${i * 0.2}s` }} />
                ))}
              </div>
            </div>

            <div className="flex gap-1 items-center z-10 scale-75 lg:scale-90">
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
                <div className="w-16 h-16 rounded-full border-2 border-orange-500/20 flex items-center justify-center animate-pulse">
                  <div className="w-12 h-12 rounded-full border border-orange-500/30 flex items-center justify-center">
                    <div className="w-2 h-2 bg-orange-500 rounded-full" />
                  </div>
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

          {/* Strategic Thoughts - Bottom Half */}
          <div className="h-3/5 rounded-3xl bg-white/5 border border-white/10 p-8 relative overflow-hidden group flex flex-col">
            <div className="flex items-center gap-4 mb-8">
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-orange-500/20 to-red-500/20 flex items-center justify-center border border-white/10">
                <span className="text-2xl animate-pulse">💡</span>
              </div>
              <div>
                <h3 className="text-xl font-black italic tracking-tighter uppercase">Negotiation Insights</h3>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-gray-500 uppercase tracking-widest font-bold">Real-time Agent Intelligence</span>
                  {contractStatus === "drafting" && (
                    <span className="flex items-center gap-1.5 px-2 py-0.5 bg-orange-500/10 border border-orange-500/20 rounded-full text-[8px] font-black text-orange-500 animate-pulse">
                      <span className="w-1 h-1 bg-orange-500 rounded-full animate-ping" />
                      DRAFTING CONTRACT
                    </span>
                  )}
                  {contractStatus === "sent" && (
                    <span className="px-2 py-0.5 bg-green-500/10 border border-green-500/20 rounded-full text-[8px] font-black text-green-500">
                      CONTRACT SENT ✅
                    </span>
                  )}
                  {contractStatus === "received" && (
                    <span className="px-2 py-0.5 bg-blue-500/10 border border-blue-500/20 rounded-full text-[8px] font-black text-blue-500 animate-bounce">
                      NEW CONTRACT RECEIVED 📩
                    </span>
                  )}
                </div>
              </div>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto pr-2 custom-scrollbar">
              {/* Specialized Contract Cards */}
              {contractStatus === "received" && sharedFiles.length > 0 && (
                <div className="p-4 rounded-2xl bg-gradient-to-br from-blue-500/20 to-blue-600/10 border-2 border-blue-500/30 animate-fade-in-scale">
                  <div className="flex justify-between items-start mb-3">
                    <span className="text-[10px] font-black text-blue-400 uppercase tracking-widest">Document Received</span>
                    <span className="text-[9px] text-blue-400/50">{sharedFiles[sharedFiles.length - 1].timestamp}</span>
                  </div>
                  <h4 className="text-sm font-bold text-white mb-2">{sharedFiles[sharedFiles.length - 1].name}</h4>
                  <div className="grid grid-cols-2 gap-2 mb-4">
                    <p className="text-[8px] text-white/40 uppercase">Price: <span className="text-white/80 font-bold">{sharedFiles[sharedFiles.length - 1].contract_data.price}</span></p>
                    <p className="text-[8px] text-white/40 uppercase">Qty: <span className="text-white/80 font-bold">{sharedFiles[sharedFiles.length - 1].contract_data.quantity}</span></p>
                  </div>
                  <a
                    href={sharedFiles[sharedFiles.length - 1].url}
                    className="block w-full py-2 bg-blue-500 hover:bg-blue-400 text-center rounded-lg text-[10px] font-black text-white uppercase tracking-wider transition-all"
                  >
                    Download Agreement
                  </a>
                </div>
              )}

              {thoughts.length > 0 ? (
                thoughts.map((thought) => (
                  <div key={thought.id} className="p-4 rounded-2xl bg-white/5 border border-white/5 animate-fade-in-up">
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`text-[8px] font-black uppercase tracking-widest ${thought.agent === "Halima" ? "text-orange-500" : "text-blue-500"}`}>{thought.agent}</span>
                      <div className="h-px flex-1 bg-white/5" />
                    </div>
                    <p className="text-xs text-gray-400 leading-relaxed italic">"{thought.text}"</p>
                  </div>
                ))
              ) : (
                <div className="h-full flex flex-col items-center justify-center opacity-20">
                  <div className="w-12 h-12 rounded-full border-2 border-dashed border-white/20 mb-4 animate-spin-slow" />
                  <p className="text-[10px] uppercase tracking-widest font-bold">Awaiting Strategic Input</p>
                </div>
              )}
            </div>

            <div className="mt-6 pt-6 border-t border-white/5 flex justify-center">
              <span className="text-[8px] uppercase tracking-[0.5em] font-black text-gray-600 italic">Harvest Protocol v4.0 // Pure Intelligence Mode</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
