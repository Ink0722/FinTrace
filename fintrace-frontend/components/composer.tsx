"use client";

import { ArrowUp, AtSign, LoaderCircle, Paperclip } from "lucide-react";
import { FormEvent, KeyboardEvent, useRef } from "react";

export function Composer({ value, onChange, onSend, running }: { value: string; onChange: (v: string) => void; onSend: () => void; running: boolean }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const submit = (e: FormEvent) => { e.preventDefault(); if (!running && value.trim()) onSend(); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!running && value.trim()) onSend(); }
  };
  return (
    <form className="composer" onSubmit={submit}>
      <textarea ref={ref} value={value} onChange={(e) => onChange(e.target.value)} onKeyDown={onKey} placeholder="向 FinTrace 提问公司、财务、事件或股权问题…" rows={1} />
      <div className="composer-bottom">
        <div className="composer-tools"><button type="button"><Paperclip size={16} /></button><button type="button"><AtSign size={16} /></button><span>FinTrace Agent</span></div>
        <button className="send-button" type="submit" disabled={running || !value.trim()}>{running ? <LoaderCircle size={16} className="spin" /> : <ArrowUp size={17} />}</button>
      </div>
    </form>
  );
}
