import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'

export function CopyButton({ text, label = 'Copy', className }: { text: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(timer)
  }, [copied])
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className={className}
      onClick={() =>
        navigator.clipboard.writeText(text).then(
          () => setCopied(true),
          () => toast.error("Couldn't copy. Select the text and copy it instead."),
        )
      }
    >
      {copied ? <Check /> : <Copy />}
      {copied ? 'Copied' : label}
    </Button>
  )
}

/** A value shown once - a new password - with a way to copy it before it's gone. */
export function SecretReveal({ value, copyText, label }: { value: string; copyText?: string; label?: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border bg-field p-2 pl-3">
      <code className="min-w-0 flex-1 font-mono text-sm break-all whitespace-pre-wrap select-all">{value}</code>
      <CopyButton text={copyText ?? value} {...(label ? { label } : {})} />
    </div>
  )
}
