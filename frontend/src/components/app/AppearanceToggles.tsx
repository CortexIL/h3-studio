import { Languages, Moon, Sun } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useLocale, useT } from '@/i18n'
import { useTheme } from '@/lib/theme'

/** Language and theme, side by side. The label of each says what it will switch to. */
export function AppearanceToggles({ className }: { className?: string }) {
  const t = useT()
  const { locale, setLocale } = useLocale()
  const { theme, toggle } = useTheme()
  const other = locale === 'he' ? 'en' : 'he'
  const switchLabel = t('locale.switch', { language: t(other === 'he' ? 'locale.he' : 'locale.en') })
  const themeLabel = t(theme === 'dark' ? 'theme.toLight' : 'theme.toDark')
  return (
    <div className={className}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button size="sm" variant="ghost" className="h-8 gap-1.5 px-2" onClick={() => setLocale(other)} aria-label={switchLabel}>
            <Languages className="size-4" />
            <span className="text-xs">{locale === 'he' ? 'EN' : 'עב'}</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent>{switchLabel}</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button size="icon" variant="ghost" className="size-8" onClick={toggle} aria-label={themeLabel}>
            {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{themeLabel}</TooltipContent>
      </Tooltip>
    </div>
  )
}
