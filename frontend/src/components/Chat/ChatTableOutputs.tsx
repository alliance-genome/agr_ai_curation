import OutputSetAsideNotice from '@/features/curation/setAside/OutputSetAsideNotice'
import type { OutputSetAsideNotice as TableNotice } from '@/features/curation/setAside/setAsideSummary'

/** Render the original transcript once, with each View action after its output. */
export default function ChatTableOutputs({ content, notices }: { content: string; notices: TableNotice[] }) {
  let start = 0
  const tables = notices.map((notice) => {
    const end = notice.contentEnd ?? content.length
    const text = content.slice(start, end)
    start = end
    return (
      <div key={`${notice.outputKey}:${end}`}>
        <span>{text}</span>
        <OutputSetAsideNotice
          counts={notice.counts}
          noun="table"
          outputName="this table"
          flowRunId={notice.flowRunId}
          documentId={notice.documentId}
        />
      </div>
    )
  })
  return <>{tables}{content.slice(start)}</>
}
