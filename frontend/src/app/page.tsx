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

  const [jumaEmotions, setJumaEmotions] = useState({ confidence: 0.85, stress: 0.12, urgency: 0.45 });
  const [alexEmotions, setAlexEmotions] = useState({ confidence: 0.62, stress: 0.78, urgency: 0.92 });

  const [negotiation] = useState({ ask: 1.25, bid: 0.95, target: 1.15 });
  const [thoughts, setThoughts] = useState<Array<{ id: string; agent: string; text: string; type: "strategy" | "insight" | "warning" }>>([]);

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
            dispatchAgents();
          }}
          className="h-full"
        >
          <DashboardContent
            negotiation={negotiation}
            jumaEmotions={jumaEmotions}
            alexEmotions={alexEmotions}
            setJumaEmotions={setJumaEmotions}
            setAlexEmotions={setAlexEmotions}
            barHeights={barHeights}
            thoughts={thoughts}
            setThoughts={setThoughts}
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
  jumaEmotions,
  alexEmotions,
  setJumaEmotions,
  setAlexEmotions,
  barHeights,
  thoughts,
  setThoughts,
  hasMounted
}: {
  negotiation: Negotiation;
  jumaEmotions: Emotions;
  alexEmotions: Emotions;
  setJumaEmotions: React.Dispatch<React.SetStateAction<Emotions>>;
  setAlexEmotions: React.Dispatch<React.SetStateAction<Emotions>>;
  barHeights: { orange: number[]; blue: number[] };
  thoughts: Thought[];
  setThoughts: React.Dispatch<React.SetStateAction<Thought[]>>;
  hasMounted: boolean;
}) {
  const participants = useParticipants();
  const room = useRoomContext();

  useEffect(() => {
    if (!room) return;

    const onDataReceived = (payload: Uint8Array, participant?: Participant) => {
      const decoder = new TextDecoder();
      try {
        const data = JSON.parse(decoder.decode(payload));
        const agentName = participant?.name || (participant?.identity.includes("juma") ? "Juma" : "Alex");

        if (data.type === "thought") {
          const id = `${agentName}-${Date.now()}`;
          setThoughts((prev: Thought[]) => [
            { id, agent: agentName, text: data.text, type: "insight" },
            ...prev.slice(0, 9),
          ]);
        } else if (data.type === "emotions") {
          const emotions = {
            confidence: data.confidence ?? 0.5,
            stress: data.stress ?? 0.1,
            urgency: data.urgency ?? 0.2
          };
          if (agentName === "Juma") setJumaEmotions(emotions);
          if (agentName === "Alex") setAlexEmotions(emotions);
        }
      } catch (e) {
        console.error("Failed to parse data message:", e);
      }
    };

    room.on("dataReceived", onDataReceived);
    return () => {
      room.off("dataReceived", onDataReceived);
    };
  }, [room, setThoughts]);

  // Filtering for agents
  const juma = participants.find(p => p.identity.includes("juma") || p.name?.toLowerCase().includes("juma"));
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
          <div className="flex gap-4 pr-6 border-r border-white/10 items-center">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${juma ? "bg-green-500" : "bg-red-500"}`} />
              <span className="text-[10px] font-mono uppercase text-gray-400">JUMA: {juma ? "IN ROOM" : "OFFLINE"}</span>
            </div>
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${alex ? "bg-green-500" : "bg-red-500"}`} />
              <span className="text-[10px] font-mono uppercase text-gray-400">ALEX: {alex ? "IN ROOM" : "OFFLINE"}</span>
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
            <EmotionRadar agentName="Juma (Seller)" scores={jumaEmotions} color="#f97316" />
            <EmotionRadar agentName="Alex (Buyer)" scores={alexEmotions} color="#3b82f6" />
          </div>

          <div className="h-48 rounded-xl bg-gradient-to-t from-orange-500/5 to-transparent border border-white/5 flex items-center justify-center">
            <div className="flex gap-1 items-center">
              {hasMounted && barHeights.orange.map((height: number, i: number) => (
                <div
                  key={i}
                  className={`w-1 bg-orange-500/40 rounded-full ${juma?.isSpeaking ? "animate-bounce" : ""}`}
                  style={{
                    height: `${juma?.isSpeaking ? height : 10}%`,
                    animationDelay: `${i * 0.1}s`,
                    transition: "height 0.2s ease"
                  }}
                />
              ))}
              <p className="mx-4 text-xs font-mono text-gray-500 uppercase">Negotiation Audio Stream Active</p>
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

        <div className="col-span-4">
          <InnerMonologue thoughts={thoughts} />

          <div className="mt-8 p-6 rounded-xl bg-orange-500 text-black">
            <h4 className="text-xs font-black uppercase mb-1">Current Directive</h4>
            <p className="text-lg font-bold leading-tight italic">&quot;Secure $1.15. Alex is bluffing about the second supplier.&quot;</p>
            <div className="mt-4 flex gap-2">
              <span className="text-[10px] font-bold border border-black/20 px-2 py-0.5 rounded">TACTICAL EMPATHY</span>
              <span className="text-[10px] font-bold border border-black/20 px-2 py-0.5 rounded">ANCHORING</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
