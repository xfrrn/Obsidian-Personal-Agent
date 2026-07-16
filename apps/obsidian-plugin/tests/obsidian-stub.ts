export class TFile {
  extension: string;

  constructor(public path: string) {
    this.extension = path.split(".").pop() ?? "";
  }
}

export class TFolder {
  children: Array<TFile | TFolder> = [];

  constructor(public path: string) {}
}

export const normalizePath = (path: string): string => path.replace(/\\/g, "/");
export const getAllTags = (): string[] => [];
export const getFrontMatterInfo = (): { exists: false; contentStart: 0 } => ({ exists: false, contentStart: 0 });
export const requestUrl = async (): Promise<never> => { throw new Error("not available in tests"); };
