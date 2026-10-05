// The owner invalidates reads on selection changes and unmount. Even a transport
// that ignores AbortSignal cannot publish a result for a superseded selection.
export class LatestRead {
  private sequence = 0;
  private controller?: AbortController;
  cancel() {
    this.sequence++;
    this.controller?.abort();
  }
  async run<T>(
    read: (signal: AbortSignal) => Promise<T>,
    publish: (value: T) => void,
  ) {
    this.cancel();
    const sequence = this.sequence;
    this.controller = new AbortController();
    try {
      const result = await read(this.controller.signal);
      if (sequence === this.sequence) publish(result);
    } catch (error) {
      if (sequence === this.sequence) throw error;
    }
  }
}
