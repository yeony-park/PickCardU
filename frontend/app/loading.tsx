export default function Loading() {
  return (
    <main className="route-loading" aria-busy="true" aria-live="polite">
      <span className="route-loading-spinner" aria-hidden="true" />
      <span>화면을 준비하고 있어요.</span>
    </main>
  );
}
