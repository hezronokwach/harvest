"use client";

import React, { useState, useEffect } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useTracks,
  useRoomContext,
} from "@livekit/components-react";
import { Participant, Track } from "livekit-client";
import PriceGapSlider from "@/components/PriceGapSlider";
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

interface Negotiation {
  ask: number;
  bid: number;
  target: number;
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

  const [timeline, setTimeline] = useState<Timeline>({ turn: 0, round: 0, maxRounds: 8 });

  const [negotiation, setNegotiation] = useState<Negotiation>({
    ask: 1.25,
    bid: 0.95,
    target: 1.15,
  });
  const [dealReached, setDealReached] = useState(false);
  const [negotiationProgress, setNegotiationProgress] = useState(0);
  const [thoughts, setThoughts] = useState<Array<{ id: string; agent: string; text: string; type: "strategy" | "insight" | "warning" }>>([]);
  const [transcripts, setTranscripts] = useState<Array<{ id: string; agent: string; text: string }>>([]);
  const [offers, setOffers] = useState<{ halima: any, alex: any }>({ halima: null, alex: null });

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
            setNegotiation={setNegotiation}
            dealReached={dealReached}
            setDealReached={setDealReached}
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
            offers={offers}
            setOffers={setOffers}
          />
          <RoomAudioRenderer />
        </LiveKitRoom>
      )}
    </main>
  );
}

function DashboardContent({
  negotiation,
  setNegotiation,
  dealReached,
  setDealReached,
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
  offers,
  setOffers
}: {
  negotiation: Negotiation;
  setNegotiation: React.Dispatch<React.SetStateAction<Negotiation>>;
  dealReached: boolean;
  setDealReached: React.Dispatch<React.SetStateAction<boolean>>;
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
  offers: { halima: any, alex: any };
  setOffers: React.Dispatch<React.SetStateAction<{ halima: any, alex: any }>>;
}) {
  const room = useRoomContext();

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
        : (participant?.name || (participant?.identity.includes("halima") || participant?.identity.includes("juma") ? "Halima" : "Alex"));

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
      } else if (data.type === "round_update") {
        const progress = (data.round / 8) * 100;
        setNegotiationProgress(progress);
      } else if (data.type === "price_update") {
        setNegotiation((prev: Negotiation) => ({
          ...prev,
          ask: data.agent === "Halima" ? data.price : prev.ask,
          bid: data.agent === "Alex" ? data.price : prev.bid,
        }));
      } else if (data.type === "offer_update") {
        setOffers((prev) => ({
          ...prev,
          [data.agent.toLowerCase()]: data.offer
        }));
        // Update price gap slider based on the new offer's price
        setNegotiation((prev: Negotiation) => ({
          ...prev,
          ask: data.agent === "Halima" ? data.offer.price : prev.ask,
          bid: data.agent === "Alex" ? data.offer.price : prev.bid,
        }));
      } else if (data.type === "deal_reached") {
        setNegotiation({
          ask: data.price,
          bid: data.price,
          target: data.price,
        });
        setDealReached(true);
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

      const agentName =
        participant.identity.includes("halima") ||
          participant.identity.includes("juma")
          ? "Halima"
          : "Alex";

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
  }, [room, setThoughts, setTranscripts, setTimeline, setNegotiationProgress, timeline]);

  useEffect(() => {
    console.warn("🔁 TIMELINE STATE CHANGED:", timeline);
  }, [timeline]);

  // Track-based role mapping for agents (LiveKit agent IDs are randomized)
  const tracks = useTracks(
    [{ source: Track.Source.Microphone, withPlaceholder: false }],
    { onlySubscribed: true }
  );

  const agentTracks = tracks.filter(t => t.participant.identity.startsWith("agent-"));
  const [halimaTrack, alexTrack] = agentTracks;

  const halimaOnline = Boolean(halimaTrack);
  const alexOnline = Boolean(alexTrack);

  const halimaSpeaking = Boolean(halimaTrack?.participant.isSpeaking);
  const alexSpeaking = Boolean(alexTrack?.participant.isSpeaking);

  return (
    <div className="max-w-7xl mx-auto">
      <div className="mb-4 p-3 bg-red-900/80 text-white font-mono text-[10px] border border-red-500 rounded flex justify-between items-center backdrop-blur-sm">
        <span>[FRONTEND DEBUG] TIMELINE STATE → turn={timeline.turn}, round={timeline.round}/{timeline.maxRounds}</span>
        <span className="opacity-50">AUTHORITY: ALEX</span>
      </div>
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
        <div className="col-span-8 space-y-8">
          <PriceGapSlider
            ask={negotiation.ask}
            bid={negotiation.bid}
            target={negotiation.target}
            dealReached={dealReached}
          />

          <NegotiationTimeline
            turn={timeline.turn}
            round={timeline.round}
            maxRounds={timeline.maxRounds}
          />

          <OfferDisplay offers={offers} />

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

        <div className="col-span-4 h-[calc(100vh-220px)] flex flex-col">
          <Transcript transcripts={transcripts} />
        </div>
      </div>
    </div>
  );
}

function NegotiationTimeline({ turn, round, maxRounds }: { turn: number; round: number; maxRounds: number }) {
  console.warn("📊 NegotiationTimeline render", { turn, round, maxRounds });
  function getPhase(round: number, max: number) {
    if (round === 0) return { label: "INTRO", color: "text-gray-400" };
    if (round === 1) return { label: "ANCHOR", color: "text-orange-500" };
    if (round < max * 0.5) return { label: "COUNTER", color: "text-blue-500" };
    if (round < max - 1) return { label: "CONVERGENCE", color: "text-green-500" };
    return { label: "CLOSING", color: "text-red-500 animate-pulse" };
  }

  const phase = getPhase(round, maxRounds);

  return (
    <div className="bg-white/5 border border-white/10 rounded-2xl p-6">
      <div className="flex justify-between items-center mb-6">
        <h3 className="text-[10px] font-bold text-gray-500 uppercase tracking-[0.3em]">Negotiation Matrix</h3>
        <div className="flex gap-2">
          {Array.from({ length: maxRounds }).map((_, i) => (
            <div
              key={i}
              className={`w-1.5 h-1.5 rounded-full transition-all duration-500 ${i < round ? "bg-orange-500 shadow-[0_0_8px_rgba(249,115,22,0.4)]" : "bg-white/10"
                }`}
            />
          ))}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-8">
        <div className="space-y-1">
          <p className="text-[8px] font-mono text-gray-600 uppercase">Phase</p>
          <p className={`text-xl font-black tracking-tighter ${phase.color}`}>{phase.label}</p>
        </div>
        <div className="space-y-1 border-l border-white/5 pl-8">
          <p className="text-[8px] font-mono text-gray-600 uppercase">Total Turns</p>
          <p className="text-xl font-black tracking-tighter text-white">{turn}</p>
        </div>
        <div className="space-y-1 border-l border-white/5 pl-8">
          <p className="text-[8px] font-mono text-gray-600 uppercase">Rounds Complete</p>
          <p className="text-xl font-black tracking-tighter text-white">
            {round} <span className="text-xs text-gray-600">/ {maxRounds}</span>
          </p>
        </div>
      </div>
    </div>
  );
}

function OfferDisplay({ offers }: { offers: { halima: any, alex: any } }) {
  return (
    <div className="grid grid-cols-2 gap-4">
      <AgentOffer label="Halima (Seller)" offer={offers.halima} color="orange" />
      <AgentOffer label="Alex (Buyer)" offer={offers.alex} color="blue" />
    </div>
  );
}

function AgentOffer({ label, offer, color }: { label: string, offer: any, color: "orange" | "blue" }) {
  if (!offer) return (
    <div className="bg-white/5 border border-white/10 rounded-xl p-4 opacity-50">
      <h4 className="text-[10px] font-bold text-gray-500 uppercase tracking-widest mb-2">{label}</h4>
      <p className="text-xs text-gray-600 italic">Waiting for strategy...</p>
    </div>
  );

  return (
    <div className={`bg-white/5 border ${color === "orange" ? "border-orange-500/20 hover:border-orange-500/40" : "border-blue-500/20 hover:border-blue-500/40"} rounded-xl p-4 relative overflow-hidden group transition-all`}>
      <div className={`absolute top-0 right-0 w-16 h-16 ${color === "orange" ? "bg-orange-500/5" : "bg-blue-500/5"} -mr-8 -mt-8 rounded-full blur-xl group-hover:scale-150 transition-transform`} />
      <h4 className={`text-[10px] font-bold ${color === "orange" ? "text-orange-500" : "text-blue-500"} uppercase tracking-widest mb-3`}>{label}</h4>
      <div className="grid grid-cols-2 gap-y-3 gap-x-2">
        <div className="space-y-0.5">
          <p className="text-[8px] text-gray-500 uppercase font-mono">Price/KG</p>
          <p className="text-lg font-black tracking-tighter text-white">${offer.price.toFixed(2)}</p>
        </div>
        <div className="space-y-0.5">
          <p className="text-[8px] text-gray-500 uppercase font-mono">Payment</p>
          <p className="text-sm font-bold text-gray-300">{offer.payment_terms.replace("_", " ")}</p>
        </div>
        <div className="space-y-0.5">
          <p className="text-[8px] text-gray-500 uppercase font-mono">Transport</p>
          <p className="text-sm font-bold text-gray-300 capitalize">{offer.transport_paid_by}</p>
        </div>
        <div className="space-y-0.5">
          <p className="text-[8px] text-gray-500 uppercase font-mono">Delivery</p>
          <p className={`text-sm font-bold ${offer.delivery_included ? "text-green-500" : "text-gray-400"}`}>
            {offer.delivery_included ? "INCLUDED" : "EXCLUDED"}
          </p>
        </div>
      </div>
    </div>
  );
}
