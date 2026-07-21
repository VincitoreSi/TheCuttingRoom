import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Card, EmptyState } from "./ui";

/* The shell's blast wall.
 *
 * React unmounts the ENTIRE tree when a render throws and nothing catches it — one bad
 * `.toFixed()` on a null the server legitimately sends produced a white screen with the
 * sidebar, header and every other working view gone with it. That is the worst possible
 * failure shape for a local tool: nothing to click, nothing to read, and the console is
 * the only clue.
 *
 * Keyed by view in App.tsx, so navigating away and back remounts cleanly and one broken
 * view never poisons the others. Error boundaries must be class components; there is no
 * hook equivalent.
 */
interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the stack in the console — this panel deliberately shows only the message, and
    // the component trace is what actually locates the bug.
    console.error("view crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Card className="p-5 md:p-6">
        <EmptyState
          title="This view hit an error"
          hint={this.state.error.message || "No message was attached to the error."}
          action={
            <Button variant="ghost" size="sm" onClick={() => this.setState({ error: null })}>
              Try again
            </Button>
          }
        />
        <p className="text-[12px] text-[var(--ink-dim)] text-center mt-2">
          The rest of the dashboard is unaffected — full stack trace is in the browser console.
        </p>
      </Card>
    );
  }
}
