type Props = {
  status: string
}

export function FileStatusIcon({ status }: Props) {
  const cls =
    status === 'running'
      ? 'task-spinner'
      : status === 'ok'
        ? 'task-done-icon'
        : status === 'failed' || status === 'warning'
          ? 'task-failed-icon'
          : status === 'pending' || status === 'queued'
            ? 'task-queued-icon'
            : 'task-idle-icon'
  return <span className={cls} aria-hidden="true" title={status} />
}
