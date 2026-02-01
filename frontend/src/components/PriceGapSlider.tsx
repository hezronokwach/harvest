"use client";

import React from "react";

interface PriceGapSliderProps {
    ask: number;
    bid: number;
    target: number;
}

export default function PriceGapSlider({ ask, bid, target }: PriceGapSliderProps) {
    // Calculate relative positions for a slider between $0.80 and $1.50
    const padding = 0.08;
    const min = Math.min(bid, ask, target) - padding;
    const max = Math.max(bid, ask, target) + padding;
    const range = max - min;

    const getPos = (val: number) => ((val - min) / range) * 100;

    return (
        <div className="p-6 rounded-xl bg-gray-900/50 border border-gray-800 backdrop-blur-md">
            <div className="flex justify-between items-end mb-6">
                <div>
                    <p className="text-xs text-gray-500 uppercase tracking-widest mb-1">Price Gap</p>
                    <h2 className="text-2xl font-bold font-mono text-white">
                        ${(ask - bid).toFixed(2)} <span className="text-sm font-normal text-gray-600">Spread</span>
                    </h2>
                </div>
                <div className="text-right">
                    <p className="text-xs text-gray-500 uppercase tracking-widest mb-1">Target</p>
                    <p className="text-xl font-mono text-green-400 font-bold">${target.toFixed(2)}</p>
                </div>
            </div>

            <div className="relative h-4 w-full bg-gray-800 rounded-full mt-8">
                {/* The Bid (Buyer) */}
                <div
                    className="absolute top-1/2 -translate-y-1/2 h-1 rounded-full bg-gradient-to-r from-blue-500 to-orange-500 transition-all duration-300"
                    style={{
                        left: `${getPos(Math.min(bid, ask))}%`,
                        width: `${Math.abs(getPos(ask) - getPos(bid))}%`,
                    }}
                >
                    <div className="w-4 h-4 rounded-full bg-blue-500 ring-4 ring-blue-500/20 shadow-[0_0_15px_rgba(59,130,246,0.5)]" />
                    <span className="mt-2 text-[10px] font-mono text-blue-400 leading-none">BID ${bid.toFixed(2)}</span>
                </div>

                {/* The Ask (Seller) */}
                <div
                    className="absolute top-1/2 -translate-y-1/2 flex flex-col items-center transition-all duration-300"
                    style={{ left: `${getPos(ask)}%` }}
                >
                    <div className="w-4 h-4 rounded-full bg-orange-500 ring-4 ring-orange-500/20 shadow-[0_0_15px_rgba(249,115,22,0.5)]" />
                    <span className="mt-2 text-[10px] font-mono text-orange-400 leading-none">ASK ${ask.toFixed(2)}</span>
                </div>

                {/* Target Marker */}
                <div
                    className="absolute top-0 bottom-0 w-0.5 bg-green-500/40 border-l border-dashed border-green-500/60"
                    style={{ left: `${getPos(target)}%` }}
                />
            </div>

            <div className="flex justify-between mt-10 text-[9px] font-mono text-gray-600 uppercase tracking-tighter">
                <span>Market Min ($0.80)</span>
                <span>Market Max ($1.50)</span>
            </div>
        </div>
    );
}
