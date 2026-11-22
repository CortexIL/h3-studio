import { useParams } from 'react-router'

/** The project the URL is inside, or undefined outside one. */
export function useProjectId(): string | undefined {
  return useParams<{ projectId: string }>().projectId
}
