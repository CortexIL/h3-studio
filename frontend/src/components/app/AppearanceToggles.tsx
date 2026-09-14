import { Languages, LayoutTemplate, Moon, Sun } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useLocale, useT } from '@/i18n'
import { useLayout } from '@/lib/layout'
import { useTheme } from '@/lib/theme'

/** Language and theme, side by side - and, inside the app, the layout. The
 *  label of each says what it will switch to. */
export function AppearanceToggles({ className, withLayout = false }: { className?: string; withLayout?: boolean }) {
  const t = useT()
  const { locale, setLocale } = useLocale()
  const { theme, toggle } = useTheme()
  const layout = useLayout((s) => s.layout)
  const toggleLayout = useLayout((s) => s.toggle)
  const layoutLabel = t(layout === 'classic' ? 'layout.toStudio' : 'layout.toClassic')
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
      {withLayout ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              size="sm"
              variant="ghost"
              className="h-8 gap-1.5 px-2"
              onClick={toggleLayout}
              aria-label={layoutLabel}
              aria-pressed={layout === 'classic'}
            >
              <LayoutTemplate className="size-4" />
              <span className="text-xs">{t(layout === 'classic' ? 'layout.classic' : 'layout.studio')}</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>{layoutLabel}</TooltipContent>
        </Tooltip>
      ) : null}
    </div>
  )
}
