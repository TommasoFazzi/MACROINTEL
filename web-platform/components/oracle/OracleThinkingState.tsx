'use client';

import { useState, useEffect, useRef } from 'react';

const STEPS = [
  'Semantic query analysis',
  'Scanning vector database',
  'Extracting relevant documents',
  'Strategic synthesis in progress',
];

// Cumulative ms before advancing to each step
const STEP_THRESHOLDS = [2000, 6000, 10000];
const SPINNER_CHARS = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

export function OracleThinkingState() {
  const [stepIndex, setStepIndex] = useState(0);
  const [spinnerIdx, setSpinnerIdx] = useState(0);
  const startRef = useRef(Date.now());
  // Stable random source count across renders
  const sourceCountRef = useRef(Math.floor(Math.random() * 23) + 25);

  useEffect(() => {
    startRef.current = Date.now();
    const id = setInterval(() => {
      const elapsed = Date.now() - startRef.current;
      setSpinnerIdx((i) => (i + 1) % SPINNER_CHARS.length);
      setStepIndex(() => {
        let step = 0;
        for (let i = 0; i < STEP_THRESHOLDS.length; i++) {
          if (elapsed >= STEP_THRESHOLDS[i]) step = i + 1;
        }
        return Math.min(step, STEPS.length - 1);
      });
    }, 120);
    return () => clearInterval(id);
  }, []);

  const label =
    stepIndex === 1
      ? `Scanning vector database (${sourceCountRef.current} sources)`
      : STEPS[stepIndex];

  return (
    <div className="flex items-center gap-2.5 py-3 max-w-2xl mx-auto w-full px-1">
      <span className="text-[#FF6B35] font-mono text-sm leading-none w-4 text-center">
        {SPINNER_CHARS[spinnerIdx]}
      </span>
      <span className="text-gray-400 text-sm">{label}...</span>
    </div>
  );
}
