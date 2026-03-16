import { useEffect, useRef } from 'react';
import { RefreshCw } from 'lucide-react';
import { createChart, CandlestickSeries, LineSeries, HistogramSeries } from 'lightweight-charts';

// Helper: Calculate EMA
const calculateEMA = (data, period) => {
    let result = [];
    if (data.length < period) return result;

    let k = 2 / (period + 1);
    let ema = data[0].close;
    for (let i = 0; i < data.length; i++) {
        ema = data[i].close * k + ema * (1 - k);
        result.push({ time: data[i].time, value: ema });
    }
    return result;
};

// Helper: Calculate Bollinger Bands
const calculateBB = (data, period, stdDev) => {
    let upper = [];
    let lower = [];
    let middle = [];
    if (data.length < period) return { upper, lower, middle };

    for (let i = period - 1; i < data.length; i++) {
        const slice = data.slice(i - period + 1, i + 1);
        const mean = slice.reduce((a, b) => a + b.close, 0) / period;
        const variance = slice.reduce((a, b) => a + Math.pow(b.close - mean, 2), 0) / period;
        const sd = Math.sqrt(variance);
        upper.push({ time: data[i].time, value: mean + stdDev * sd });
        lower.push({ time: data[i].time, value: mean - stdDev * sd });
        middle.push({ time: data[i].time, value: mean });
    }
    return { upper, lower, middle };
};

// Helper: Calculate RSI
const calculateRSI = (data, period) => {
    let result = [];
    if (data.length < period) return result;

    let gains = 0;
    let losses = 0;

    for (let i = 1; i < data.length; i++) {
        const difference = data[i].close - data[i - 1].close;
        if (difference >= 0) {
            gains += difference;
        } else {
            losses -= difference;
        }

        if (i >= period) {
            if (i > period) {
                const prevDifference = data[i - 1].close - data[i - 2].close;
                if (prevDifference >= 0) {
                    gains = (gains * (period - 1) + (difference >= 0 ? difference : 0)) / period;
                    losses = (losses * (period - 1) + (difference < 0 ? -difference : 0)) / period;
                }
            } else {
                gains /= period;
                losses /= period;
            }

            const rs = gains / (losses || 1);
            result.push({ time: data[i].time, value: 100 - 100 / (1 + rs) });
        }
    }
    return result;
};

export function TradingChart({ data, symbol, activeTrade, replayTrade }) {
    const chartContainerRef = useRef();
    const chartRef = useRef();
    const seriesRef = useRef({});
    const lastSymbolRef = useRef();
    const priceLinesRef = useRef([]);

    useEffect(() => {
        if (!chartContainerRef.current || !data || data.length === 0) return;

        // 1. Initialize or Reset Chart on symbol change
        if (!chartRef.current || lastSymbolRef.current !== symbol) {
            if (chartRef.current) {
                chartRef.current.remove();
            }

            const chart = createChart(chartContainerRef.current, {
                layout: {
                    background: { color: 'transparent' },
                    textColor: '#a1a1aa',
                    fontSize: 10,
                },
                grid: {
                    vertLines: { color: '#18181b' },
                    horzLines: { color: '#18181b' },
                },
                autoSize: true,
                timeScale: {
                    borderColor: '#27272a',
                    timeVisible: true,
                    secondsVisible: false,
                },
                crosshair: {
                    mode: 0,
                    vertLine: { labelBackgroundColor: '#4f46e5' },
                    horzLine: { labelBackgroundColor: '#4f46e5' },
                }
            });

            chartRef.current = chart;
            lastSymbolRef.current = symbol;

            // Setup Series
            seriesRef.current.candlestick = chart.addSeries(CandlestickSeries, {
                upColor: '#10b981',
                downColor: '#ef4444',
                borderVisible: false,
                wickUpColor: '#10b981',
                wickDownColor: '#ef4444',
                priceLineVisible: true,
            });

            seriesRef.current.ema9 = chart.addSeries(LineSeries, {
                color: '#4ade80', // Bright Green for fast EMA
                lineWidth: 1,
                title: 'EMA 9',
                lastValueVisible: false,
                priceLineVisible: false,
            });

            seriesRef.current.ema21 = chart.addSeries(LineSeries, {
                color: '#f59e0b',
                lineWidth: 1,
                title: 'EMA 21',
                lastValueVisible: false,
                priceLineVisible: false,
            });

            seriesRef.current.ema50 = chart.addSeries(LineSeries, {
                color: '#3b82f6',
                lineWidth: 1,
                title: 'EMA 50',
                lastValueVisible: false,
                priceLineVisible: false,
            });

            seriesRef.current.ema200 = chart.addSeries(LineSeries, {
                color: '#ef4444',
                lineWidth: 1,
                title: 'EMA 200',
                lastValueVisible: false,
                priceLineVisible: false,
            });

            seriesRef.current.bbUpper = chart.addSeries(LineSeries, {
                color: 'rgba(34, 211, 238, 0.4)',
                lineWidth: 1,
                lineStyle: 2,
                title: 'BB Upper',
                lastValueVisible: false,
                priceLineVisible: false,
            });

            seriesRef.current.bbMid = chart.addSeries(LineSeries, {
                color: 'rgba(34, 211, 238, 0.15)',
                lineWidth: 1,
                lineStyle: 2,
                title: 'BB Mid',
                lastValueVisible: false,
                priceLineVisible: false,
            });

            seriesRef.current.bbLower = chart.addSeries(LineSeries, {
                color: 'rgba(34, 211, 238, 0.4)',
                lineWidth: 1,
                lineStyle: 2,
                title: 'BB Lower',
                lastValueVisible: false,
                priceLineVisible: false,
            });

            seriesRef.current.volume = chart.addSeries(HistogramSeries, {
                color: '#27272a',
                priceFormat: { type: 'volume' },
                priceScaleId: '', // overlay
            });
            seriesRef.current.volume.priceScale().applyOptions({
                scaleMargins: { top: 0.8, bottom: 0 },
            });

            seriesRef.current.rsi = chart.addSeries(LineSeries, {
                color: '#a855f7',
                lineWidth: 1,
                title: 'RSI 14',
                priceScaleId: 'rsi',
            });
            chart.priceScale('rsi').applyOptions({
                scaleMargins: { top: 0.85, bottom: 0.05 },
                borderVisible: false,
            });

            // Initial fit content
            chart.timeScale().fitContent();
        }

        const chart = chartRef.current;
        const series = seriesRef.current;

        // 2. Update Series Data (Persistent Zoom)
        series.candlestick.applyOptions({
            priceFormat: {
                type: 'price',
                precision: (symbol.includes('EURUSD') || symbol.includes('GBPUSD')) ? 5 : symbol.includes('JPY') ? 3 : 2,
                minMove: (symbol.includes('EURUSD') || symbol.includes('GBPUSD')) ? 0.00001 : symbol.includes('JPY') ? 0.001 : 0.01,
            }
        });
        series.candlestick.setData(data);

        const ema9Data = calculateEMA(data, 9);
        series.ema9.setData(ema9Data);

        const ema21Data = calculateEMA(data, 21);
        series.ema21.setData(ema21Data);

        const ema50Data = calculateEMA(data, 50);
        series.ema50.setData(ema50Data);

        const ema200Data = calculateEMA(data, 200);
        series.ema200.setData(ema200Data);

        const bb = calculateBB(data, 20, 2);
        series.bbUpper.setData(bb.upper);
        series.bbMid.setData(bb.middle);
        series.bbLower.setData(bb.lower);

        series.volume.setData(data.map(d => ({
            time: d.time,
            value: d.volume || 0,
            color: d.close >= d.open ? '#10b98122' : '#ef444422'
        })));

        const rsiData = calculateRSI(data, 14);
        series.rsi.setData(rsiData);

        // 3. Update Trade Lines (SL/TP/REPLAY)
        // Clear old lines
        priceLinesRef.current.forEach(line => series.candlestick.removePriceLine(line));
        priceLinesRef.current = [];

        const tradeToRender = replayTrade || activeTrade;

        const updateMarkers = (s, m) => {
            if (!s) return;
            if (typeof s.setMarkers === 'function') {
                s.setMarkers(m);
            } else if (typeof s.createSeriesMarkers === 'function') {
                if (!s._markersPlugin) {
                    s._markersPlugin = s.createSeriesMarkers();
                }
                s._markersPlugin.set(m || []);
            }
        };

        if (tradeToRender) {
            const isReplay = !!replayTrade;

            // Entry Level
            if (tradeToRender.price_open) {
                const entryLine = series.candlestick.createPriceLine({
                    price: tradeToRender.price_open,
                    color: isReplay ? '#818cf8' : '#6366f1',
                    lineWidth: 2,
                    lineStyle: 0,
                    axisLabelVisible: true,
                    title: `ENTRY: ${tradeToRender.price_open.toFixed(5)}`,
                });
                priceLinesRef.current.push(entryLine);
            }

            // SL Level
            if (tradeToRender.sl) {
                const slLine = series.candlestick.createPriceLine({
                    price: tradeToRender.sl,
                    color: '#ef4444',
                    lineWidth: 1,
                    lineStyle: 2,
                    axisLabelVisible: true,
                    title: `SL: ${tradeToRender.sl.toFixed(5)}`,
                });
                priceLinesRef.current.push(slLine);
            }

            // TP Level
            if (tradeToRender.tp) {
                const tpLine = series.candlestick.createPriceLine({
                    price: tradeToRender.tp,
                    color: '#10b981',
                    lineWidth: 1,
                    lineStyle: 2,
                    axisLabelVisible: true,
                    title: `TP: ${tradeToRender.tp.toFixed(5)}`,
                });
                priceLinesRef.current.push(tpLine);
            }

            // Exit Level (for Replay)
            if (isReplay && tradeToRender.price_close) {
                const exitLine = series.candlestick.createPriceLine({
                    price: tradeToRender.price_close,
                    color: tradeToRender.profit >= 0 ? '#10b981' : '#f43f5e',
                    lineWidth: 2,
                    lineStyle: 0,
                    axisLabelVisible: true,
                    title: `EXIT: ${tradeToRender.price_close.toFixed(5)} (${tradeToRender.profit.toFixed(2)}€)`,
                });
                priceLinesRef.current.push(exitLine);
            }

            // Markers (Arrows)
            const markers = [];
            if (tradeToRender.time_in) {
                const timeIn = typeof tradeToRender.time_in === 'string' ? parseInt(tradeToRender.time_in) : tradeToRender.time_in;
                markers.push({
                    time: timeIn,
                    position: tradeToRender.type === 'BUY' ? 'belowBar' : 'aboveBar',
                    color: tradeToRender.type === 'BUY' ? '#10b981' : '#f43f5e',
                    shape: tradeToRender.type === 'BUY' ? 'arrowUp' : 'arrowDown',
                    text: `IN: ${tradeToRender.type}`,
                    size: 2
                });
            }

            if (isReplay && tradeToRender.time_out) {
                const timeOut = typeof tradeToRender.time_out === 'string' ? parseInt(tradeToRender.time_out) : tradeToRender.time_out;
                markers.push({
                    time: timeOut,
                    position: tradeToRender.type === 'BUY' ? 'aboveBar' : 'belowBar',
                    color: '#6366f1',
                    shape: tradeToRender.type === 'BUY' ? 'arrowDown' : 'arrowUp',
                    text: 'OUT',
                    size: 2
                });
            }

            if (markers.length > 0) {
                updateMarkers(series.candlestick, markers.sort((a, b) => a.time - b.time));
            } else {
                updateMarkers(series.candlestick, []);
            }

            // Scroll to trade if it's a replay
            if (isReplay && tradeToRender.time_in) {
                const timeIn = typeof tradeToRender.time_in === 'string' ? parseInt(tradeToRender.time_in) : tradeToRender.time_in;
                setTimeout(() => {
                    chart.timeScale().scrollToPosition(0, false);
                }, 100);
            }
        } else {
            updateMarkers(series.candlestick, []);
        }

        // No manual resize needed with autoSize: true
        return () => { };
    }, [data, activeTrade, symbol, replayTrade]);

    // Final Cleanup on Unmount
    useEffect(() => {
        return () => {
            if (chartRef.current) {
                chartRef.current.remove();
                chartRef.current = null;
            }
        };
    }, []);

    return (
        <div className="w-full h-full relative bg-[#050505] rounded-[2rem] border border-zinc-900/50 overflow-hidden shadow-2xl">
            <div ref={chartContainerRef} className="w-full h-full" />
            {!data || data.length === 0 ? (
                <div className="absolute inset-0 flex items-center justify-center bg-black/40 backdrop-blur-sm z-50">
                    <div className="flex flex-col items-center gap-3">
                        <RefreshCw className="animate-spin text-indigo-500" size={32} />
                        <span className="text-[10px] font-black text-zinc-500 uppercase tracking-[0.5em] italic">Synchronizing Fleet Telemetry...</span>
                    </div>
                </div>
            ) : null}
            <div className="absolute top-6 left-8 flex gap-4 pointer-events-none z-10">
                <div className="flex items-center gap-2 bg-zinc-900/80 backdrop-blur-md px-3 py-1.5 rounded-xl border border-zinc-800 shadow-xl">
                    <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    <span className="text-[10px] font-black text-white uppercase italic tracking-widest">{symbol} CORE ANALYSIS</span>
                </div>
                <div className="flex items-center gap-4 bg-zinc-900/50 backdrop-blur-md px-3 py-1.5 rounded-xl border border-zinc-800 text-[9px] font-bold text-zinc-500 uppercase tracking-tighter">
                    <span className="flex items-center gap-1.5"><div className="w-2 h-0.5 bg-[#4ade80]" /> EMA 9</span>
                    <span className="flex items-center gap-1.5"><div className="w-2 h-0.5 bg-[#f59e0b]" /> EMA 21</span>
                    <span className="flex items-center gap-1.5"><div className="w-2 h-0.5 bg-[#3b82f6]" /> EMA 50</span>
                    <span className="flex items-center gap-1.5"><div className="w-2 h-0.5 bg-[#ef4444]" /> EMA 200</span>
                    <span className="flex items-center gap-1.5"><div className="w-2 h-0.5 bg-[#22d3ee]" /> BB 20,2</span>
                    <span className="flex items-center gap-1.5"><div className="w-2 h-0.5 bg-[#a855f7]" /> RSI 14</span>
                </div>
            </div>
        </div>
    );
}
