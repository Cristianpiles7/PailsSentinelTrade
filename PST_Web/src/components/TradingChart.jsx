import { useEffect, useRef, useState } from 'react';
import { RefreshCw, Pencil, Trash2 } from 'lucide-react';
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

export function TradingChart({ data, symbol, timeframe, activeTrade, replayTrade }) {
    const chartContainerRef = useRef();
    const chartRef = useRef();
    const seriesRef = useRef({});
    const lastSymbolRef = useRef();
    const priceLinesRef = useRef([]);

    // Drawing State (SVG Overlay)
    const [isDrawingMode, setIsDrawingMode] = useState(false);
    const [trendLines, setTrendLines] = useState([]);
    const [currentLine, setCurrentLine] = useState(null);
    const [viewportTick, setViewportTick] = useState(0);
    const [selectedLineId, setSelectedLineId] = useState(null);
    const [draggingPoint, setDraggingPoint] = useState(null);

    // Clear lines when symbol changes and Fetch from DB
    useEffect(() => {
        let isMounted = true;
        setTrendLines([]);
        setCurrentLine(null);
        setIsDrawingMode(false);
        
        // Fetch saved lines from DB
        const loadLines = async () => {
            try {
                const token = localStorage.getItem('pst_token') || '';
                const base = import.meta.env.DEV ? "http://127.0.0.1:8000/api" : "/api";
                console.log(`[PST] Cargando líneas para ${symbol}...`);
                const res = await fetch(`${base}/user_levels/${symbol}`, {
                    headers: { 'X-PST-Token': token }
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const remoteLines = await res.json();
                
                if (isMounted && Array.isArray(remoteLines)) {
                    console.log(`[PST] Líneas recibidas para ${symbol}:`, remoteLines);
                    setTrendLines(remoteLines);
                }
            } catch (e) { 
                if (isMounted) console.error('[PST] Error fetching user lines:', e);
            }
        };
        loadLines();
        return () => { isMounted = false; };
    }, [symbol]);

    // Helper to Auto Save
    const saveLinesToDB = async (linesToSave) => {
        try {
            const token = localStorage.getItem('pst_token') || '';
            const base = import.meta.env.DEV ? "http://127.0.0.1:8000/api" : "/api";
            console.log(`[PST] Guardando ${linesToSave.length} líneas en DB...`);
            const res = await fetch(`${base}/user_levels/${symbol}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-PST-Token': token },
                body: JSON.stringify({ lines: linesToSave })
            });
            if (!res.ok) {
                const errorData = await res.json().catch(() => ({}));
                throw new Error(`HTTP ${res.status}: ${errorData.detail || 'Unknown Error'}`);
            }
            console.log(`[PST] Guardado exitoso para ${symbol}`);
        } catch (e) { console.error('[PST] Error saving user lines:', e) }
    };

    // Helper para extraer time y logical dado un X
    const getPointFromX = (x) => {
        const chart = chartRef.current;
        let time = chart.timeScale().coordinateToTime(x);
        const logical = chart.timeScale().coordinateToLogical(x);
        
        if (time === null && logical !== null && data && data.length > 0) {
            const lastBarTime = data[data.length - 1].time;
            const lastBarCoord = chart.timeScale().timeToCoordinate(lastBarTime);
            const lastBarLogical = chart.timeScale().coordinateToLogical(lastBarCoord);
            if (lastBarLogical !== null) {
                const diffLogical = logical - lastBarLogical;
                time = lastBarTime + (diffLogical * 300);
            }
        }
        return { time, logical };
    };

    // Global KeyListener for Deletion and Deselection
    useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.key === 'Delete' || e.key === 'Backspace') {
                if (selectedLineId !== null) {
                    setTrendLines(prev => {
                        const updated = prev.filter(l => l.id !== selectedLineId);
                        saveLinesToDB(updated);
                        return updated;
                    });
                    setSelectedLineId(null);
                }
            }
            if (e.key === 'Escape') {
                setSelectedLineId(null);
                setIsDrawingMode(false);
                setCurrentLine(null);
            }
        };
        document.addEventListener('keydown', handleKeyDown);
        return () => document.removeEventListener('keydown', handleKeyDown);
    }, [selectedLineId, symbol]);

    // Global Click Listener for Deselection
    useEffect(() => {
        const handleGlobalClick = (e) => {
            const tags = ['line', 'circle', 'rect', 'text', 'g'];
            if (!tags.includes(e.target.tagName.toLowerCase())) {
                setSelectedLineId(null);
            }
        };
        document.addEventListener('mousedown', handleGlobalClick);
        return () => document.removeEventListener('mousedown', handleGlobalClick);
    }, []);

    // Dragging Logic
    useEffect(() => {
        const handleMouseMove = (e) => {
            if (!draggingPoint || !chartRef.current || !seriesRef.current?.candlestick) return;
            const containerRect = chartContainerRef.current.getBoundingClientRect();
            const x = e.clientX - containerRect.left;
            const y = e.clientY - containerRect.top;

            const { time, logical } = getPointFromX(x);
            const price = seriesRef.current.candlestick.coordinateToPrice(y);

            setTrendLines(prev => prev.map(line => {
                if (line.id !== draggingPoint.lineId) return line;
                const newPoint = { time, logical, price };
                if (draggingPoint.pointKey === 'p1') return { ...line, p1: newPoint };
                if (draggingPoint.pointKey === 'p2') return { ...line, p2: newPoint };
                // O arrastrar linea entera
                return line;
            }));
        };

        const handleMouseUp = () => {
            if (draggingPoint) {
                setDraggingPoint(null);
                // Cuando terminamos el D&D, auto guardamos
                setTrendLines(prev => {
                    saveLinesToDB(prev);
                    return prev;
                });
            }
        };

        if (draggingPoint) {
            document.addEventListener('mousemove', handleMouseMove);
            document.addEventListener('mouseup', handleMouseUp);
        }
        return () => {
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        };
    }, [draggingPoint]);

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

    const lastCandleRef = useRef(null);

    // Sincronizar la base de la vela cuando cambia el historial
    useEffect(() => {
        if (data && data.length > 0) {
            lastCandleRef.current = { ...data[data.length - 1] };
        }
    }, [data]);

    // 4. Inyección de Ticks Pro (Latency Zero - Bypassing React)
    useEffect(() => {
        const getTimeframeSeconds = (tf) => {
            const unit = tf[0];
            const val = parseInt(tf.substring(1));
            if (unit === 'M') return val * 60;
            if (unit === 'H') return val * 3600;
            if (unit === 'D') return 86400;
            return 60;
        };

        const handleTick = (e) => {
            const msg = e.detail;
            if (!msg || !symbol) return;
            
            // Normalización para soportar sufijos (.m, .cash, etc.)
            const msgSym = msg.symbol.toUpperCase().split('.')[0];
            const activeSym = symbol.toUpperCase().split('.')[0];
            if (msgSym !== activeSym) return;

            if (!seriesRef.current?.candlestick || !lastCandleRef.current) return;

            const candlestickSeries = seriesRef.current.candlestick;
            const currentCandle = lastCandleRef.current;
            
            const interval = getTimeframeSeconds(timeframe || 'M1');
            
            // Usar el tiempo del broker si existe, si no, respaldo local
            const nowSeconds = msg.time ? Math.floor(msg.time / 1000) : Math.floor(Date.now() / 1000);
            const candleStartTime = Math.floor(nowSeconds / interval) * interval;

            if (candleStartTime > currentCandle.time) {
                const newCandle = {
                    time: candleStartTime,
                    open: msg.bid,
                    high: msg.bid,
                    low: msg.bid,
                    close: msg.bid
                };
                lastCandleRef.current = newCandle;
                candlestickSeries.update(newCandle);
                console.info(`[Chart] New Candle Core: ${symbol} @ ${candleStartTime}`);
            } else {
                const updatedCandle = {
                    ...currentCandle,
                    high: Math.max(currentCandle.high, msg.bid),
                    low: Math.min(currentCandle.low, msg.bid),
                    close: msg.bid
                };
                lastCandleRef.current = updatedCandle;
                candlestickSeries.update(updatedCandle);
            }
        };

        window.addEventListener('pst-tick', handleTick);
        return () => window.removeEventListener('pst-tick', handleTick);
    }, [symbol, timeframe]);

    // Handle Clicks, Mouse Move and Panning for SVG Drawing
    useEffect(() => {
        if (!chartRef.current || !seriesRef.current.candlestick) return;
        const chart = chartRef.current;
        const series = seriesRef.current.candlestick;

        const clickHandler = (param) => {
            if (!param.point) return;
            if (!isDrawingMode) return;

            const price = series.coordinateToPrice(param.point.y);
            const { time, logical } = getPointFromX(param.point.x);
            
            if (price !== null && (time !== undefined || logical !== undefined)) {
                if (!currentLine) {
                    // Primer clic (Inicio de línea)
                    setCurrentLine({
                        p1: { time, logical, price },
                        p2: { x: param.point.x, y: param.point.y }
                    });
                } else {
                    // Segundo clic (Fin de línea)
                    const newLine = {
                        id: Date.now(),
                        p1: currentLine.p1,
                        p2: { time, logical, price },
                        mode: 'BOTH'
                    };
                    setTrendLines(prev => {
                        const updated = [...prev, newLine];
                        saveLinesToDB(updated);
                        return updated;
                    });
                    
                    setCurrentLine(null);
                    setIsDrawingMode(false); 
                }
            }
        };

        const moveHandler = (param) => {
            if (!isDrawingMode || !currentLine) return;
            if (param.point) {
                setCurrentLine(prev => prev ? ({
                    ...prev,
                    p2: { x: param.point.x, y: param.point.y }
                }) : null);
            }
        };

        // Forzar renderizado SVG cuando el usuario mueva o haga zoom
        const viewportHandler = () => setViewportTick(t => t + 1);

        chart.subscribeClick(clickHandler);
        chart.subscribeCrosshairMove(moveHandler);
        const ts = chart.timeScale();
        
        // Compatibilidad entre distintas versiones de lightweight-charts (v3, v4, v5)
        if (typeof ts.subscribeVisibleLogicalRangeChange === 'function') {
            ts.subscribeVisibleLogicalRangeChange(viewportHandler);
        } else if (typeof ts.subscribeLogicalRangeChange === 'function') {
            ts.subscribeLogicalRangeChange(viewportHandler);
        } else if (typeof ts.subscribeVisibleTimeRangeChange === 'function') {
            ts.subscribeVisibleTimeRangeChange(viewportHandler);
        }
        
        if (typeof ts.subscribeSizeChange === 'function') {
            ts.subscribeSizeChange(viewportHandler);
        }

        return () => {
            chart.unsubscribeClick(clickHandler);
            chart.unsubscribeCrosshairMove(moveHandler);
            if (typeof ts.unsubscribeVisibleLogicalRangeChange === 'function') ts.unsubscribeVisibleLogicalRangeChange(viewportHandler);
            else if (typeof ts.unsubscribeLogicalRangeChange === 'function')    ts.unsubscribeLogicalRangeChange(viewportHandler);
            else if (typeof ts.unsubscribeVisibleTimeRangeChange === 'function') ts.unsubscribeVisibleTimeRangeChange(viewportHandler);
            
            if (typeof ts.unsubscribeSizeChange === 'function') ts.unsubscribeSizeChange(viewportHandler);
        };
    }, [isDrawingMode, currentLine]); // Re-bind con el currentLine correcto

    // Helper para transformar (time/price) a pixeles X,Y
    const getPointCoords = (point, chart, series) => {
        if (!chart || !series || !point) return { x: 0, y: 0 };
        if (point.x !== undefined && point.y !== undefined) return { x: point.x, y: point.y };
        
        let x = null;
        if (point.time !== undefined && point.time !== null) {
            x = chart.timeScale().timeToCoordinate(point.time);
            
            // Si devuelve null, significa que point.time está en el futuro o un hueco que no está en las velas.
            if (x === null && data && data.length > 0) {
                const lastBar = data[data.length - 1];
                const diffSeconds = point.time - lastBar.time;
                // Calculamos desplazamiento logico asumiendo resolucion M5 (300)
                const diffLogical = diffSeconds / 300; 
                
                const lastBarCoord = chart.timeScale().timeToCoordinate(lastBar.time);
                let lastBarLogical = null;
                if (lastBarCoord !== null) {
                    lastBarLogical = chart.timeScale().coordinateToLogical(lastBarCoord);
                }
                
                if (lastBarLogical !== null) {
                    x = chart.timeScale().logicalToCoordinate(lastBarLogical + diffLogical);
                }
            }
        } 
        
        if (x === null && point.logical !== undefined && point.logical !== null) {
            x = chart.timeScale().logicalToCoordinate(point.logical);
        }
        
        const y = series.priceToCoordinate(point.price);
        return { x: x || 0, y: y || 0 };
    };

    // Renderizado manual de los trazos SVG
    const renderSVGOverlay = () => {
        const chart = chartRef.current;
        const series = seriesRef.current?.candlestick;
        if (!chart || !series) return null;

        return (
            <svg className="absolute inset-0 w-full h-full pointer-events-none z-10">
                {trendLines.map(line => {
                    const isSelected = selectedLineId === line.id;
                    const p1 = getPointCoords(line.p1, chart, series);
                    const p2 = getPointCoords(line.p2, chart, series);
                    
                    // Definición de colores según el modo
                    const modeColors = {
                        'SUPPORT': '#10b981',
                        'RESISTANCE': '#ef4444',
                        'BOTH': '#eab308'
                    };
                    const lineColor = isSelected ? "#ffffff" : (modeColors[line.mode] || '#eab308');
                    const midX = (p1.x + p2.x) / 2;
                    const midY = (p1.y + p2.y) / 2;

                    return (
                        <g key={line.id} className="pointer-events-auto">
                            {/* Hitbox ensanchado transparente para atrapar clics facilmente */}
                            <line 
                                x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
                                stroke="transparent" strokeWidth="15"
                                className="cursor-pointer"
                                onClick={(e) => { e.stopPropagation(); setSelectedLineId(line.id); }}
                            />
                            {/* Línea visible real */}
                            <line 
                                x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y} 
                                stroke={lineColor} 
                                strokeWidth={isSelected ? "3" : "2"} 
                                strokeLinecap="round"
                                className="pointer-events-none"
                            />
                            
                            {/* Etiqueta de Modo (Solo si está seleccionada o para identificar rápido) */}
                            {isSelected && (
                                <g 
                                    transform={`translate(${midX}, ${midY - 20})`}
                                    className="cursor-pointer select-none"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        const nextMode = { 'BOTH': 'SUPPORT', 'SUPPORT': 'RESISTANCE', 'RESISTANCE': 'BOTH' };
                                        setTrendLines(prev => {
                                            const updated = prev.map(l => l.id === line.id ? { ...l, mode: nextMode[l.mode || 'BOTH'] } : l);
                                            saveLinesToDB(updated);
                                            return updated;
                                        });
                                    }}
                                >
                                    <rect x="-35" y="-10" width="70" height="20" rx="4" fill="#18181b" stroke={lineColor} strokeWidth="1" />
                                    <text x="0" y="4" textAnchor="middle" fill="white" fontSize="10" fontWeight="bold" className="uppercase italic">
                                        {line.mode || 'BOTH'}
                                    </text>
                                </g>
                            )}
                            {/* Circulos editables de anclaje (solo aparecen cuando está seleccionada) */}
                            {isSelected && (
                                <>
                                    {/* Hitbox P1 */}
                                    <circle cx={p1.x} cy={p1.y} r="15" fill="transparent" 
                                        className="cursor-move" 
                                        onMouseDown={(e) => { e.stopPropagation(); setDraggingPoint({ lineId: line.id, pointKey: 'p1' }); }} 
                                    />
                                    <circle 
                                        cx={p1.x} cy={p1.y} r="6" fill="#ffffff" stroke={lineColor} strokeWidth="2"
                                        className="pointer-events-none" 
                                    />

                                    {/* Hitbox P2 */}
                                    <circle cx={p2.x} cy={p2.y} r="15" fill="transparent" 
                                        className="cursor-move" 
                                        onMouseDown={(e) => { e.stopPropagation(); setDraggingPoint({ lineId: line.id, pointKey: 'p2' }); }} 
                                    />
                                    <circle 
                                        cx={p2.x} cy={p2.y} r="6" fill="#ffffff" stroke={lineColor} strokeWidth="2"
                                        className="pointer-events-none" 
                                    />
                                </>
                            )}
                        </g>
                    );
                })}
                {currentLine && (
                    <line 
                        x1={getPointCoords(currentLine.p1, chart, series).x} 
                        y1={getPointCoords(currentLine.p1, chart, series).y} 
                        x2={getPointCoords(currentLine.p2, chart, series).x} 
                        y2={getPointCoords(currentLine.p2, chart, series).y} 
                        stroke="#eab308" 
                        strokeWidth="2" 
                        strokeDasharray="4 4"
                    />
                )}
            </svg>
        );
    };

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
            {renderSVGOverlay()}
            
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

            {/* Drawing Tools Floating Panel */}
            <div className="absolute right-8 top-1/2 -translate-y-1/2 flex flex-col gap-3 pointer-events-auto z-20">
                <button 
                    onClick={() => {
                        setIsDrawingMode(!isDrawingMode);
                        if(isDrawingMode) setCurrentLine(null); // Cancel current draw
                    }}
                    className={`p-3 rounded-xl border transition-all duration-300 shadow-xl ${isDrawingMode ? 'bg-indigo-500 text-white border-indigo-400 scale-110 shadow-indigo-500/50 cursor-crosshair' : 'bg-zinc-800/80 text-zinc-400 border-zinc-700 hover:bg-zinc-700 hover:text-white'}`}
                    title="Dibujar Soporte/Resistencia/Diagonales"
                >
                    <Pencil size={20} />
                </button>
                {trendLines.length > 0 && (
                    <button 
                        onClick={() => {
                            setTrendLines([]);
                            setCurrentLine(null);
                            saveLinesToDB([]); // BORRAMOS BBDD
                        }}
                        className="p-3 rounded-xl bg-zinc-800/80 text-rose-500 border border-zinc-700 hover:bg-rose-500/10 transition-all duration-300 shadow-xl"
                        title="Borrar líneas"
                    >
                        <Trash2 size={20} />
                    </button>
                )}
            </div>
        </div>
    );
}
