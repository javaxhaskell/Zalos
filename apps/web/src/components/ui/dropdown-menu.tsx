"use client";

import * as React from "react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

interface DropdownMenuProps {
  readonly trigger: React.ReactNode;
  readonly children: React.ReactNode;
  readonly align?: "start" | "end";
  readonly side?: "top" | "bottom";
  readonly disabled?: boolean;
  readonly className?: string;
  readonly menuClassName?: string;
  readonly onOpenChange?: (open: boolean) => void;
}

export function DropdownMenu({
  trigger,
  children,
  align = "start",
  side = "bottom",
  disabled = false,
  className,
  menuClassName,
  onOpenChange,
}: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    onOpenChange?.(open);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  return (
    <div ref={containerRef} className={cn("relative inline-flex shrink-0", className)}>
      <div
        onClick={() => {
          if (!disabled) setOpen((prev) => !prev);
        }}
      >
        {trigger}
      </div>
      {open && !disabled ? (
        <div
          role="menu"
          className={cn(
            "absolute z-[100] min-w-[8rem] max-h-40 overflow-y-auto rounded-md border border-slate-200 bg-white py-0 shadow-lg",
            side === "top" ? "bottom-full mb-1" : "top-full mt-1",
            align === "end" ? "right-0" : "left-0",
            menuClassName,
          )}
        >
          {React.Children.map(children, (child) => {
            if (!React.isValidElement(child)) return child;
            return React.cloneElement(child as React.ReactElement<DropdownMenuItemProps>, {
              onSelect: () => {
                (child.props as DropdownMenuItemProps).onSelect?.();
                setOpen(false);
              },
            });
          })}
        </div>
      ) : null}
    </div>
  );
}

interface DropdownMenuItemProps {
  readonly children: React.ReactNode;
  readonly onSelect?: () => void;
  readonly className?: string;
}

export function DropdownMenuItem({
  children,
  onSelect,
  className,
}: DropdownMenuItemProps) {
  return (
    <button
      type="button"
      role="menuitem"
      className={cn(
        "flex w-full items-center px-2.5 py-1 text-left text-xs leading-tight text-slate-700 transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none",
        className,
      )}
      onClick={onSelect}
    >
      {children}
    </button>
  );
}
